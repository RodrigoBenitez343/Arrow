"""
Recursive Vision-Enhanced Reasoning Engine

This module provides a lightweight recursive reasoning system that enables
small language models to perform complex reasoning by iteratively analyzing
screen context (JSON format from YOLO/OCR) alongside the task description.

Key features:
- Minimal cycles (1-3 by default) to avoid long wait times
- JSON-structured screen context analysis
- Task decomposition into actionable sub-steps
- Integration with existing memory systems
"""

import os
import time
import json
import logging
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)

VISION_REASONING_PROMPT = """You are a task reasoning assistant analyzing screen context and user tasks.

SCREEN CONTEXT (JSON from Vision Analysis):
{vision_context}

USER TASK:
{task}

PREVIOUS REASONING (if any):
{previous_reasoning}

INSTRUCTIONS:
1. Analyze the screen context thoroughly - note all UI elements, their positions, and any relevant text
2. Break down the task into specific, actionable steps
3. For each step, identify what action to take and on which UI element
4. Output your reasoning as a JSON object

Output format:
{{
  "analysis": "What you observe on screen and how it relates to the task",
  "task_breakdown": ["Step 1: specific action", "Step 2: specific action", ...],
  "confidence": 0.0-1.0,
  "needs_more_context": true/false,
  "additional_queries": ["optional question if more context needed", ...]
}}
"""

SYNTHESIZE_PROMPT = """You are synthesizing the results of recursive reasoning into a final actionable output.

RECURSIVE REASONING HISTORY:
{reasoning_history}

VISION CONTEXT:
{vision_context}

ORIGINAL TASK:
{task}

FINAL INSTRUCTIONS:
Based on all the reasoning cycles, produce a concise, actionable plan that can be executed by an automation agent.

Output format:
{{
  "final_plan": ["Actionable step 1", "Actionable step 2", ...],
  "summary": "Brief summary of reasoning",
  "confidence": 0.0-1.0
}}
"""


class RecursiveVisionReasoner:
    """
    A lightweight recursive reasoning engine for vision-enhanced task analysis.

    This reasoner takes a small model through multiple brief reasoning cycles,
    each time analyzing the screen context (JSON) and previous reasoning to
    refine the understanding and produce actionable steps.
    """

    def __init__(
        self,
        ollama_client,
        max_cycles: int = 3,
        vision_model: str = "minicpm-v:latest",
        reasoning_model: Optional[str] = None,
        temperature: float = 0.3,
        confidence_threshold: float = 0.85
    ):
        """
        Initialize the recursive vision reasoner.

        Args:
            ollama_client: OllamaClient instance for API calls
            max_cycles: Maximum reasoning cycles (default 3, keep low for speed)
            vision_model: Model to use for vision analysis pass
            reasoning_model: Model to use for reasoning (defaults to vision_model)
            temperature: Temperature for generation (lower = more focused)
            confidence_threshold: Stop early if confidence exceeds this
        """
        self.ollama_client = ollama_client
        self.max_cycles = max(1, min(max_cycles, 5))
        self.vision_model = vision_model
        self.reasoning_model = reasoning_model or vision_model
        self.temperature = temperature
        self.confidence_threshold = confidence_threshold
        self._reasoning_history: List[Dict[str, Any]] = []

    def reset(self):
        """Reset the reasoning history for a new task."""
        self._reasoning_history = []

    def _format_vision_context(self, vision_data: Any) -> str:
        """Format vision data into a readable context string."""
        if isinstance(vision_data, dict):
            return json.dumps(vision_data, indent=2, default=str)
        elif isinstance(vision_data, str):
            try:
                parsed = json.loads(vision_data)
                return json.dumps(parsed, indent=2, default=str)
            except (json.JSONDecodeError, TypeError):
                return str(vision_data)
        else:
            return str(vision_data) if vision_data else "No vision context available"

    def _call_llm(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        system: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Call the LLM with the given prompt and return parsed JSON response.

        Returns None on failure.
        """
        try:
            model = self.vision_model if images else self.reasoning_model

            if images:
                response = self.ollama_client.generate(
                    model=model,
                    prompt=prompt,
                    images=images,
                    temperature=self.temperature,
                    max_tokens=2048
                )
            else:
                response = self.ollama_client.generate(
                    model=self.reasoning_model,
                    prompt=prompt,
                    temperature=self.temperature,
                    max_tokens=2048
                )

            text = None
            if isinstance(response, dict):
                text = response.get('response') or response.get('text')
            elif isinstance(response, str):
                text = response

            if not text:
                logger.warning("Empty response from LLM")
                return None

            text = text.strip()
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = text[json_start:json_end]
                return json.loads(json_str)

            logger.warning(f"Could not parse JSON from response: {text[:200]}...")
            return {"raw_response": text, "parse_error": True}

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return None

    async def reason(
        self,
        task: str,
        vision_context: Any,
        images: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Perform recursive reasoning on the task with vision context.

        Args:
            task: The user's task description
            vision_context: JSON/dict from YOLO+OCR screen analysis
            images: Optional list of screenshot paths for vision models

        Returns:
            Dict containing reasoning results with keys:
            - reasoning_history: List of cycle results
            - final_plan: Synthesized action plan
            - summary: Brief summary
            - confidence: Final confidence score
            - cycles_used: Number of reasoning cycles performed
        """
        self.reset()

        formatted_vision = self._format_vision_context(vision_context)
        current_task = task
        accumulated_reasoning = ""

        for cycle in range(self.max_cycles):
            logger.info(f"RecursiveVisionReasoner: Starting cycle {cycle + 1}/{self.max_cycles}")

            prompt = VISION_REASONING_PROMPT.format(
                vision_context=formatted_vision,
                task=current_task,
                previous_reasoning=accumulated_reasoning if accumulated_reasoning else "None yet - this is the first analysis."
            )

            result = self._call_llm(prompt, images=images if cycle == 0 else None)

            if result is None:
                logger.warning(f"Cycle {cycle + 1} failed, attempting to continue...")
                result = {"analysis": "Analysis failed", "confidence": 0.5, "needs_more_context": False}

            result["cycle"] = cycle + 1
            self._reasoning_history.append(result)

            confidence = result.get("confidence", 0.5)
            needs_more = result.get("needs_more_context", False)

            if confidence >= self.confidence_threshold and cycle >= 1:
                logger.info(f"Confidence threshold met ({confidence}), stopping early")
                break

            if needs_more and cycle < self.max_cycles - 1:
                additional_queries = result.get("additional_queries", [])
                if additional_queries:
                    current_task = f"{task}\n\nAdditional context needed:\n" + "\n".join(additional_queries)
                    accumulated_reasoning += f"\n--- Cycle {cycle + 1} ---\n{result.get('analysis', '')}\n"

            if "task_breakdown" in result and result["task_breakdown"]:
                accumulated_reasoning += f"\n--- Cycle {cycle + 1} ---\n{result.get('analysis', '')}\nSteps identified: {len(result['task_breakdown'])}\n"

        final_result = self._synthesize_results(task, formatted_vision)
        return final_result

    def _synthesize_results(self, task: str, vision_context: str) -> Dict[str, Any]:
        """Synthesize all reasoning cycles into a final plan."""
        history_text = "\n".join([
            f"Cycle {r.get('cycle', i+1)}: {r.get('analysis', '')}"
            f" (confidence: {r.get('confidence', 'N/A')})"
            for i, r in enumerate(self._reasoning_history)
        ])

        prompt = SYNTHESIZE_PROMPT.format(
            reasoning_history=history_text,
            vision_context=vision_context,
            task=task
        )

        result = self._call_llm(prompt, images=None)

        final_plan = []
        summary = ""
        confidence = 0.0

        if result and not result.get("parse_error"):
            final_plan = result.get("final_plan", [])
            summary = result.get("summary", "")
            confidence = result.get("confidence", 0.0)
        else:
            all_breakdowns = []
            for r in self._reasoning_history:
                if "task_breakdown" in r:
                    all_breakdowns.extend(r["task_breakdown"])
            if all_breakdowns:
                final_plan = all_breakdowns[:10]
                summary = "Synthesized from recursive reasoning"
                confidence = sum(r.get("confidence", 0.5) for r in self._reasoning_history) / len(self._reasoning_history)

        return {
            "reasoning_history": self._reasoning_history,
            "final_plan": final_plan if isinstance(final_plan, list) else [str(final_plan)],
            "summary": summary,
            "confidence": confidence,
            "cycles_used": len(self._reasoning_history),
            "raw_synthesis": result
        }

    def reason_stream(
        self,
        task: str,
        vision_context: Any,
        images: Optional[List[str]] = None,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """
        Perform reasoning with streaming updates via callback.

        callback receives dicts with keys: type, data
        """
        self.reset()

        formatted_vision = self._format_vision_context(vision_context)
        current_task = task
        accumulated_reasoning = ""

        for cycle in range(self.max_cycles):
            if callback:
                callback({"type": "cycle_start", "cycle": cycle + 1, "total": self.max_cycles})

            prompt = VISION_REASONING_PROMPT.format(
                vision_context=formatted_vision,
                task=current_task,
                previous_reasoning=accumulated_reasoning if accumulated_reasoning else "None yet."
            )

            result = self._call_llm(prompt, images=images if cycle == 0 else None)

            if result is None:
                result = {"analysis": "Analysis failed in this cycle", "confidence": 0.5, "needs_more_context": False}

            result["cycle"] = cycle + 1
            self._reasoning_history.append(result)

            if callback:
                callback({"type": "cycle_result", "cycle": cycle + 1, "result": result})

            confidence = result.get("confidence", 0.5)
            needs_more = result.get("needs_more_context", False)

            if confidence >= self.confidence_threshold and cycle >= 1:
                if callback:
                    callback({"type": "threshold_met", "confidence": confidence})
                break

            if needs_more and cycle < self.max_cycles - 1:
                additional_queries = result.get("additional_queries", [])
                if additional_queries:
                    current_task = f"{task}\n\nAdditional context needed:\n" + "\n".join(additional_queries)
                    accumulated_reasoning += f"\n--- Cycle {cycle + 1} ---\n{result.get('analysis', '')}\n"

            if "task_breakdown" in result:
                accumulated_reasoning += f"\n--- Cycle {cycle + 1} ---\n{result.get('analysis', '')}\n"

        final_result = self._synthesize_results(task, formatted_vision)

        if callback:
            callback({"type": "final_result", "result": final_result})

        return final_result


def quick_vision_reason(
    ollama_client,
    task: str,
    vision_context: Any,
    images: Optional[List[str]] = None,
    model: str = "minicpm-v:latest",
    max_cycles: int = 2
) -> Dict[str, Any]:
    """
    Quick one-shot vision-enhanced reasoning.

    Use this for simple tasks that don't need full recursive analysis.
    """
    reasoner = RecursiveVisionReasoner(
        ollama_client=ollama_client,
        max_cycles=max_cycles,
        vision_model=model,
        reasoning_model=model,
        temperature=0.3,
        confidence_threshold=0.9
    )
    return reasoner.reason(task, vision_context, images)
