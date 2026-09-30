from __future__ import annotations

from typing import Callable, Iterable, TYPE_CHECKING

import torch

if TYPE_CHECKING:
    from torch import Tensor

from .base import MmprojModel, ModelBase, gguf


@ModelBase.register("LocateAnythingForConditionalGeneration")
class LocateAnythingModel(MmprojModel):
    """NVIDIA LocateAnything-3B: MoonViT-SO-400M vision tower + Eagle MLP + Qwen2.5-3B.

    Vision tower is the same MoonViT lineage as Kimi-VL/K2.5; this conversion mirrors
    conversion/kimivl.py::KimiK25Model. HF tensor names use ``vision_model.*`` and
    ``mlp1.*`` prefixes (vs Kimi's ``vision_tower.*`` / ``mm_projector.*``) — we rename
    on the fly so the existing TensorNameMap entries for ``vision_tower.encoder.*``
    pick them up.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert self.hparams_vision is not None
        self.merge_kernel_size = tuple(self.hparams_vision.get("merge_kernel_size", [2, 2]))
        self.patch_size = self.hparams_vision.get("patch_size", 14)
        pos_emb_h = self.hparams_vision.get("init_pos_emb_height", 64)
        self.hparams_vision["image_size"] = pos_emb_h * self.patch_size

    def set_gguf_parameters(self):
        super().set_gguf_parameters()
        assert self.hparams_vision is not None

        self.gguf_writer.add_clip_projector_type(gguf.VisionProjectorType.LOCATEANYTHING)

        self.gguf_writer.add_uint32("vision.pos_emb_height", self.hparams_vision.get("init_pos_emb_height", 64))
        self.gguf_writer.add_uint32("vision.pos_emb_width", self.hparams_vision.get("init_pos_emb_width", 64))

        self.gguf_writer.add_vision_use_gelu(True)
        self.gguf_writer.add_vision_attention_layernorm_eps(self.hparams_vision.get("layer_norm_eps", 1e-5))
        self.gguf_writer.add_vision_projector_scale_factor(self.merge_kernel_size[0])

        in_token_limit = self.preprocessor_config.get("in_token_limit", 4096)
        pixels_per_patch = self.patch_size ** 2
        self.gguf_writer.add_vision_min_pixels(8 * pixels_per_patch)
        self.gguf_writer.add_vision_max_pixels(in_token_limit * pixels_per_patch)

    @staticmethod
    def permute(weights: Tensor, n_head: int) -> Tensor:
        """MoonViT interleaved-pair → llama.cpp sectioned RoPE format.

        Reshape head_dim into ``(head_dim//4, 2_xy, 2_pair)`` and swap the xy/pair
        axes so the first half of head_dim ends up holding all the x-rotated rows
        and the second half all y. After this rewrite, the runtime ``build_rope_2d``
        helper (sectioned: first half by pos_a, second half by pos_b) produces the
        same numerical output as the reference's interleaved RoPE.

        Same formula as ``KimiK25Model.permute`` (kimivl.py); MoonViT is identical.
        """
        out_dim, in_dim = weights.shape
        head_dim = out_dim // n_head
        w = weights.reshape(n_head, head_dim // 4, 2, 2, in_dim)
        w = w.permute(0, 2, 1, 3, 4)
        return w.reshape(out_dim, in_dim)

    @classmethod
    def filter_tensors(cls, item: tuple[str, Callable[[], Tensor]]) -> tuple[str, Callable[[], Tensor]] | None:
        name, gen = item
        if not (name.startswith("vision_model.") or name.startswith("mlp1.")):
            return None
        return super().filter_tensors(item)

    def modify_tensors(self, data_torch: Tensor, name: str, bid: int | None) -> Iterable[tuple[str, Tensor]]:
        assert self.hparams_vision is not None
        n_head = self.hparams_vision.get("num_attention_heads", 16)

        # Rename HF prefixes so existing TensorNameMap entries (which target
        # vision_tower.* / mm_projector.*) pick the tensors up.
        # vision_model.* → vision_tower.*  (matches the Kimi-VL TensorNameMap)
        if name.startswith("vision_model."):
            name = "vision_tower." + name[len("vision_model."):]
        # mlp1.{j}.* → mm_projector.proj.{j}.*  (Kimi-K2.5 mapping pattern)
        elif name.startswith("mlp1."):
            name = "mm_projector.proj." + name[len("mlp1."):]

        # Pos-emb is stored (H, W, C) in the safetensors. Keep it 3D so the
        # GGUF tensor has ne = (C, W, H) — the runtime's
        # ``resize_position_embeddings_3d`` reads pos_embd->ne[0]=C, ne[1]=W,
        # ne[2]=H, then permutes + ggml_interpolate to the actual patch grid.
        # No reshape needed — torch's last axis (C) maps to ggml ne[0].

        # Split packed wqkv → wq, wk, wv. Permute Q/K from interleaved-pair
        # MoonViT layout into the sectioned layout that ``build_rope_2d`` expects.
        # V is untouched.
        if "wqkv" in name:
            qkv_dim = data_torch.shape[0] // 3
            head_dim = qkv_dim // n_head

            if "weight" in name:
                wq = self.permute(data_torch[:qkv_dim, :], n_head)
                wk = self.permute(data_torch[qkv_dim:2 * qkv_dim, :], n_head)
                wv = data_torch[2 * qkv_dim:, :]
                yield from super().modify_tensors(wq, name.replace("wqkv", "wq"), bid)
                yield from super().modify_tensors(wk, name.replace("wqkv", "wk"), bid)
                yield from super().modify_tensors(wv, name.replace("wqkv", "wv"), bid)
                return
            elif "bias" in name:
                bq = data_torch[:qkv_dim].reshape(n_head, head_dim // 4, 2, 2).permute(0, 2, 1, 3).reshape(-1)
                bk = data_torch[qkv_dim:2 * qkv_dim].reshape(n_head, head_dim // 4, 2, 2).permute(0, 2, 1, 3).reshape(-1)
                bv = data_torch[2 * qkv_dim:]
                yield from super().modify_tensors(bq, name.replace("wqkv", "wq"), bid)
                yield from super().modify_tensors(bk, name.replace("wqkv", "wk"), bid)
                yield from super().modify_tensors(bv, name.replace("wqkv", "wv"), bid)
                return

        # Eagle MLP: rename HF tensors to names the existing TensorNameMap recognises.
        #   mlp1.0 (LayerNorm) → mm_projector.pre_norm     → V_MM_INP_NORM (tensor_mapping.py:1728)
        #   mlp1.1 (Linear)    → mm_projector.proj.linear_1 → V_MMPROJ bid=1 (tensor_mapping.py:1374)
        #   mlp1.3 (Linear)    → mm_projector.proj.linear_2 → V_MMPROJ bid=2
        if "mm_projector.proj.0." in name:
            name = name.replace("mm_projector.proj.0.", "mm_projector.pre_norm.")
        elif "mm_projector.proj.1." in name:
            name = name.replace("mm_projector.proj.1.", "mm_projector.proj.linear_1.")
        elif "mm_projector.proj.3." in name:
            name = name.replace("mm_projector.proj.3.", "mm_projector.proj.linear_2.")

        yield from super().modify_tensors(data_torch, name, bid)
