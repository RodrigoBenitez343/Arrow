"""Evaluator fine-tuning: port of `utils/crag/scripts/train_evaluator.py`.

Upstream trains ``t5-large`` as a one-logit sequence classifier on
``question [SEP] passage \t label`` rows (label 0/1 -> -1/+1), 8 epochs,
batch 12, AdamW lr 1e-4, linear schedule, saving one directory per epoch.

Two upstream bugs are fixed in the port (documented, not silent):
``--batch_size`` / ``--num_epochs`` were parsed and then ignored (hardcoded
12 / 8), and ``--seed`` was optional while the seeds were applied
unconditionally.

Only needed for a dataset-level reproduction - the app's runtime evaluator is
Laya (``AI/crag/evaluator.py``).
"""

import argparse
import random


def get_data(file):
    """``(content, label)`` from ``content \t label`` rows (upstream shape)."""
    content, label = [], []
    with open(file, "r", encoding="utf-8") as f:
        for line in f.readlines():
            line = line.rstrip("\n")
            if not line.strip():
                continue
            c, l = line.rsplit("\t", 1)
            content.append(c)
            label.append((int(l.strip()) - 0.5) * 2)
    return content, label


def data_preprocess(file, tokenizer, max_length=512):
    import pandas as pd

    content, label = get_data(file)
    data = pd.DataFrame({"content": content, "label": label})
    train_data = tokenizer(data.content.to_list(), padding="max_length",
                           max_length=max_length, truncation=True,
                           return_tensors="pt")
    return train_data, data.label.to_list()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_file", required=True)
    parser.add_argument("--save_path", required=True)
    parser.add_argument("--model", default="t5-large")
    parser.add_argument("--batch_size", type=int, default=12)
    parser.add_argument("--num_epochs", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch
    from torch.optim import AdamW
    from torch.utils.data import DataLoader, TensorDataset
    from transformers import (T5ForSequenceClassification, T5Tokenizer,
                              get_scheduler)

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    tokenizer = T5Tokenizer.from_pretrained(args.model)
    model = T5ForSequenceClassification.from_pretrained(
        args.model, num_labels=1)
    train_data, train_label = data_preprocess(args.train_file, tokenizer)

    train = TensorDataset(train_data["input_ids"],
                          train_data["attention_mask"],
                          torch.tensor(train_label))
    train_dataloader = DataLoader(train, batch_size=args.batch_size,
                                  shuffle=True, sampler=None)
    optimizer = AdamW(model.parameters(), lr=args.lr)
    num_training_steps = args.num_epochs * len(train_dataloader)
    print(num_training_steps)
    lr_scheduler = get_scheduler(
        name="linear", optimizer=optimizer, num_warmup_steps=0,
        num_training_steps=num_training_steps)

    device = (torch.device("cuda:0") if torch.cuda.is_available()
              else torch.device("cpu"))
    model.to(device)
    for i in range(args.num_epochs):
        total_loss = 0
        model.train()
        for step, batch in enumerate(train_dataloader):
            if step % 10 == 0 and step != 0:
                print("step: ", step, "  loss:",
                      total_loss / (step * args.batch_size))
            b_input_ids = batch[0].to(device)
            b_input_mask = batch[1].to(device)
            b_labels = batch[2].to(device)
            model.zero_grad()
            outputs = model(b_input_ids, attention_mask=b_input_mask,
                            labels=b_labels)
            loss = outputs.loss
            loss.mean().backward()
            total_loss += loss.mean().item()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            lr_scheduler.step()
            optimizer.zero_grad()
        print("avg_loss:", total_loss / max(1, len(train_dataloader)))
        model.save_pretrained("%s/ep%d" % (args.save_path, i))
        tokenizer.save_pretrained("%s/ep%d" % (args.save_path, i))


if __name__ == "__main__":
    main()
