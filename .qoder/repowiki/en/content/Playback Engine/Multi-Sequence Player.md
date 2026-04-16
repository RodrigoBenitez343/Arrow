# Multi-Sequence Player

<cite>
**Referenced Files in This Document**
- [multi_sequence_player.py](file://LoOper/player/multi_sequence_player.py)
- [sequence_executor.py](file://LoOper/player/multi_sequence/sequence_executor.py)
- [workflow_graph.py](file://LoOper/player/multi_sequence/workflow_graph.py)
- [conditional_fallback_handler.py](file://LoOper/player/multi_sequence/conditional_fallback_handler.py)
- [llm_executor.py](file://LoOper/player/multi_sequence/llm_executor.py)
- [core.py](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the multi-sequence player component responsible for orchestrating complex automation workflows that combine multiple sequences, conditional logic, and fallback mechanisms. It covers workflow graph execution, dependency resolution, conditional branching, memory sharing across executions, fallback handling, and integration with AI services and computer vision systems. The goal is to help both developers and operators understand how the system executes multi-sequence chains reliably, recover from failures, and scale to complex scenarios.

## Project Structure
The multi-sequence player is implemented under the player module with a modular design:
- MultiSequencePlayer: orchestrator that initializes and coordinates all subsystems
- SequenceExecutor: executes individual sequences and manages app focus/sandbox sessions
- WorkflowExecutor: executes workflow graphs with dependency-aware scheduling
- ConditionalFallbackHandler: encapsulates computer vision triggers, loops, and fallback orchestration
- LLMExecutor: modularized LLM node execution
- WorkflowGraphBuilder and Navigator: construct and navigate the workflow graph

```mermaid
graph TB
MSP["MultiSequencePlayer<br/>orchestrator"] --> WGB["WorkflowGraphBuilder<br/>builds graph"]
MSP --> WFN["WorkflowNavigator<br/>navigates graph"]
MSP --> WFEX["WorkflowExecutor<br/>executes graph"]
MSP --> SEQEX["SequenceExecutor<br/>executes sequences"]
MSP --> CFH["ConditionalFallbackHandler<br/>fallbacks & triggers"]
WFEX --> SEQEX
WFEX --> CFH
WFEX --> LLMEX["LLMExecutor<br/>LLM nodes"]
```

**Diagram sources**
- [multi_sequence_player.py:32-135](file://LoOper/player/multi_sequence_player.py#L32-L135)
- [workflow_graph.py:1-10](file://LoOper/player/multi_sequence/workflow_graph.py#L1-L10)
- [conditional_fallback_handler.py:20-43](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L20-L43)
- [core.py:14-38](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L14-L38)

**Section sources**
- [multi_sequence_player.py:1-120](file://LoOper/player/multi_sequence_player.py#L1-L120)
- [workflow_graph.py:1-10](file://LoOper/player/multi_sequence/workflow_graph.py#L1-L10)

## Core Components
- MultiSequencePlayer: central coordinator that loads chains, preloads assets, sets up sandbox contexts, and delegates to modular executors. It also shares selection memory across components to ensure deterministic candidate selection across sequences and fallbacks.
- SequenceExecutor: executes a single sequence node, resolves paths, manages app focus and sandbox sessions, injects fallback sequences, and coordinates with the fallback handler for pre/post conditions.
- WorkflowExecutor: schedules and executes nodes in a workflow graph respecting dependencies, supports conditional branching, LLM-specific chaining rules, and maintains a ready-set for dependency-driven execution.
- ConditionalFallbackHandler: provides presence/absence/OCR triggers, conditional loops, and fallback orchestration. It caches validation results and resolves paths for assets.
- LLMExecutor: modularized executor for LLM nodes, integrated into the workflow execution pipeline.

**Section sources**
- [multi_sequence_player.py:32-135](file://LoOper/player/multi_sequence_player.py#L32-L135)
- [sequence_executor.py:17-25](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L25)
- [core.py:14-38](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L14-L38)
- [conditional_fallback_handler.py:20-43](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L20-L43)
- [llm_executor.py:1-12](file://LoOper/player/multi_sequence/llm_executor.py#L1-L12)

## Architecture Overview
The multi-sequence player implements a layered architecture:
- Orchestration layer: MultiSequencePlayer initializes components and coordinates execution
- Execution layer: WorkflowExecutor drives graph execution; SequenceExecutor handles sequence execution; ConditionalFallbackHandler manages triggers and fallbacks
- Integration layer: LLMExecutor integrates AI services; computer vision triggers enable conditional logic; sandbox management enables remote automation

```mermaid
sequenceDiagram
participant User as "Caller"
participant MSP as "MultiSequencePlayer"
participant WFEX as "WorkflowExecutor"
participant SEQEX as "SequenceExecutor"
participant CFH as "ConditionalFallbackHandler"
participant LLMEX as "LLMExecutor"
User->>MSP : play_chain(stop_flag, preserve_selection_memory)
MSP->>MSP : reset components, prepare sandbox
alt workflow_graph exists
MSP->>WFEX : execute_workflow(stop_flag, action_handlers)
WFEX->>SEQEX : execute_sequence_node(...)
SEQEX->>CFH : handle pre/post conditions
SEQEX-->>WFEX : completion status
WFEX->>LLMEX : execute_llm_node(...)
LLMEX-->>WFEX : completion status
WFEX-->>MSP : workflow result
else legacy format
MSP->>SEQEX : execute_sequence(...)
SEQEX->>CFH : handle pre/post conditions
SEQEX-->>MSP : completion status
end
MSP-->>User : result
```

**Diagram sources**
- [multi_sequence_player.py:192-358](file://LoOper/player/multi_sequence_player.py#L192-L358)
- [core.py:51-316](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L51-L316)
- [sequence_executor.py:901-1274](file://LoOper/player/multi_sequence/sequence_executor.py#L901-L1274)
- [conditional_fallback_handler.py:100-184](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L100-L184)

## Detailed Component Analysis

### MultiSequencePlayer
Responsibilities:
- Loads chain configuration in legacy or new dict format
- Preloads chains/sequences for performance
- Builds workflow graph from sequences and conditional nodes
- Initializes modular components: ConditionalFallbackHandler, WorkflowNavigator, LLMExecutor, SequenceExecutor, WorkflowExecutor
- Shares selection memory across components to maintain deterministic behavior
- Manages sandbox lifecycle for remote automation
- Supports ComoRAG engine readiness and memory clearing

Key behaviors:
- Preloading: Uses JSON cache to preload chains and sequences to reduce IO overhead
- Memory sharing: Propagates selection_memory to fallback_handler, sequence_executor, and workflow_executor
- Sandbox management: Begins/ends sandbox sessions and propagates agent URLs to action handlers
- Conditional LLM handling: Detects and waits for ComoRAG readiness before clearing memory

```mermaid
classDiagram
class MultiSequencePlayer {
+chain_config
+chain_file_dir
+sequences
+conditional_nodes
+llm_nodes
+tts_nodes
+chain_import_nodes
+code_nodes
+container_nodes
+workflow_graph
+fallback_handler
+workflow_navigator
+llm_executor
+sequence_executor
+workflow_executor
+selection_memory
+play_chain(stop_flag, preserve_selection_memory)
+_play_legacy_chain(stop_flag)
+_handle_pre_sequence_conditions(item, stop_flag, advanced_conditions)
+_handle_post_sequence_conditions(item, stop_flag, advanced_conditions)
}
```

**Diagram sources**
- [multi_sequence_player.py:32-135](file://LoOper/player/multi_sequence_player.py#L32-L135)

**Section sources**
- [multi_sequence_player.py:39-135](file://LoOper/player/multi_sequence_player.py#L39-L135)
- [multi_sequence_player.py:192-358](file://LoOper/player/multi_sequence_player.py#L192-L358)

### SequenceExecutor
Responsibilities:
- Execute a single sequence node with loop support and delays
- Resolve sequence file paths across multiple locations
- Manage app focus and sandbox sessions for both local and remote targets
- Inject fallback sequences into actions and coordinate fallback execution
- Share selection memory with SequencePlayer instances

Key behaviors:
- Path resolution: Searches multiple directories and supports frozen builds
- App focus: Ensures target application is in foreground or launches sandbox agent
- Fallback injection: Adds chain-level and action-specific fallback sequences
- Conditional pre/post: Delegates to ConditionalFallbackHandler for presence/absence/OCR checks

```mermaid
flowchart TD
Start([Execute Sequence]) --> ResolvePath["Resolve sequence file path"]
ResolvePath --> LoadData["Load sequence data (cached or disk)"]
LoadData --> FocusApp["Ensure app focus or launch sandbox"]
FocusApp --> PreConditions["Check pre-sequence conditions"]
PreConditions --> |Skip| PostConditions["Run post-sequence conditions"]
PreConditions --> |Continue| LoopStart["Start loop iteration"]
LoopStart --> InjectFallbacks["Inject fallback sequences"]
InjectFallbacks --> Play["Play sequence with fallback callback"]
Play --> LoopEnd{"More loops?"}
LoopEnd --> |Yes| LoopStart
LoopEnd --> |No| PostConditions
PostConditions --> End([Done])
```

**Diagram sources**
- [sequence_executor.py:937-1274](file://LoOper/player/multi_sequence/sequence_executor.py#L937-L1274)

**Section sources**
- [sequence_executor.py:17-25](file://LoOper/player/multi_sequence/sequence_executor.py#L17-L25)
- [sequence_executor.py:937-1274](file://LoOper/player/multi_sequence/sequence_executor.py#L937-L1274)

### WorkflowExecutor
Responsibilities:
- Execute workflow graphs with dependency-aware scheduling
- Evaluate conditional nodes and manage branching
- Respect LLM-specific chaining rules and typing synchronization
- Maintain ready-set of nodes whose dependencies are satisfied
- Record node summaries and last executed node metadata

Key behaviors:
- Dependency resolution: Finds starting nodes and iteratively selects ready nodes
- Conditional branching: Stores chosen branch in node data and marks unchosen branches as skipped
- LLM chaining: Delays non-LLM outputs until LLM concatenation completes
- Typing synchronization: Defers execution while LLM is "typing"

```mermaid
sequenceDiagram
participant WFEX as "WorkflowExecutor"
participant Graph as "Workflow Graph"
participant Ready as "Ready Set"
participant Comp as "Completed Set"
WFEX->>Graph : Find starting nodes
Graph-->>WFEX : Starting nodes
WFEX->>Ready : Add starting nodes
loop While ready not empty
WFEX->>WFEX : Select next node (priority)
WFEX->>Graph : Execute node based on type
alt Node success
WFEX->>Comp : Mark node completed
WFEX->>WFEX : Compute next nodes (branching, connections)
WFEX->>Ready : Add newly ready nodes
else Node failure
WFEX-->>WFEX : Stop workflow
end
end
WFEX-->>WFEX : Return completion status
```

**Diagram sources**
- [core.py:51-316](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L51-L316)

**Section sources**
- [core.py:14-38](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L14-L38)
- [core.py:51-316](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L51-L316)

### ConditionalFallbackHandler
Responsibilities:
- Provide modular components for triggers (presence, absence, OCR), conditional loops, and node execution
- Cache validation results keyed by screen hash and trigger configuration
- Resolve asset paths relative to chain file directory
- Handle pre/post-sequence conditions with wait semantics

Key behaviors:
- Trigger evaluation: Supports timeout-based presence checks and polling-based absence/OCR checks
- Conditional loops: Execute loops before/after sequences with configurable conditions
- Backward compatibility: Delegates legacy methods to new modular components

```mermaid
classDiagram
class ConditionalFallbackHandler {
+template_matcher
+validation_cache
+path_resolver
+presence_trigger
+absence_trigger
+ocr_trigger
+conditional_loop
+node_executor
+handle_pre_sequence_conditions(item, stop_flag, advanced_conditions)
+handle_post_sequence_conditions(item, stop_flag, advanced_conditions)
+execute_conditional_loop(loop_config, sequence_player)
}
```

**Diagram sources**
- [conditional_fallback_handler.py:20-43](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L20-L43)

**Section sources**
- [conditional_fallback_handler.py:20-43](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L20-L43)
- [conditional_fallback_handler.py:100-184](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L100-L184)

### LLMExecutor Integration
Responsibilities:
- Modularized LLM node execution integrated into WorkflowExecutor
- Provides variable storage and retrieval for LLM outputs
- Coordinates with WorkflowExecutor for typing synchronization and chaining

Integration points:
- Variables: LLMExecutor exposes get/set variable APIs consumed by WorkflowExecutor
- Chaining: LLM nodes can influence downstream execution order and prioritization

**Section sources**
- [llm_executor.py:1-12](file://LoOper/player/multi_sequence/llm_executor.py#L1-L12)
- [core.py:99-116](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L99-L116)

## Dependency Analysis
The multi-sequence player exhibits strong modularity with clear separation of concerns:
- MultiSequencePlayer depends on WorkflowGraphBuilder, WorkflowNavigator, WorkflowExecutor, SequenceExecutor, and ConditionalFallbackHandler
- WorkflowExecutor depends on SequenceExecutor, ConditionalFallbackHandler, and LLMExecutor
- SequenceExecutor depends on ConditionalFallbackHandler and uses JSON cache for performance
- ConditionalFallbackHandler depends on TemplateMatching, ValidationCache, PathResolver, and trigger modules

```mermaid
graph TB
MSP["MultiSequencePlayer"] --> WGB["WorkflowGraphBuilder"]
MSP --> WFN["WorkflowNavigator"]
MSP --> WFEX["WorkflowExecutor"]
MSP --> SEQEX["SequenceExecutor"]
MSP --> CFH["ConditionalFallbackHandler"]
WFEX --> SEQEX
WFEX --> CFH
WFEX --> LLMEX["LLMExecutor"]
SEQEX --> CFH
CFH --> TM["TemplateMatching"]
CFH --> VC["ValidationCache"]
CFH --> PR["PathResolver"]
```

**Diagram sources**
- [multi_sequence_player.py:14-21](file://LoOper/player/multi_sequence_player.py#L14-L21)
- [workflow_graph.py](file://LoOper/player/multi_sequence/workflow_graph.py#L7)
- [conditional_fallback_handler.py:9-17](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L9-L17)
- [core.py:5-12](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L5-L12)

**Section sources**
- [multi_sequence_player.py:14-21](file://LoOper/player/multi_sequence_player.py#L14-L21)
- [workflow_graph.py](file://LoOper/player/multi_sequence/workflow_graph.py#L7)
- [conditional_fallback_handler.py:9-17](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L9-L17)
- [core.py:5-12](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L5-L12)

## Performance Considerations
- Preloading: Chain and sequence preloading reduces repeated IO; verify cache stats to confirm effectiveness
- Caching: JSON cache and validation cache minimize redundant loads and repeated template matches
- Path resolution: Efficient multi-path search avoids unnecessary filesystem scans
- Conditional polling: Reduced polling intervals (e.g., 0.02s) improve responsiveness without excessive CPU usage
- Memory sharing: Selection memory propagation prevents repeated visual searches and improves determinism
- Sandboxing: Reuse of sandbox agent URLs avoids repeated session setup costs

Recommendations:
- Monitor cache hit rates and adjust preload strategies for frequently reused chains
- Use extra_delay judiciously to balance throughput and stability
- Prefer presence/absence triggers with timeouts to avoid indefinite polling
- Keep LLM outputs synchronized to prevent premature downstream execution

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and strategies:
- Circular dependencies: WorkflowExecutor detects stalls when no nodes are ready; review node connections and conditional branches
- Execution timeouts: Use presence/absence/OCR wait conditions with explicit timeouts; configure appropriate polling intervals
- Memory leaks: Ensure sandbox sessions are ended; verify agent URLs are cleared and sessions closed
- Visual matching failures: Inject fallback sequences per action; verify image paths and thresholds; leverage validation cache
- LLM synchronization: WorkflowExecutor defers execution while LLM is "typing"; ensure LLM nodes complete before downstream nodes

Recovery strategies:
- Graceful stop: All major components honor stop_flag; use it to terminate long-running operations
- Fallback execution: SequenceExecutor invokes fallback sequences with shared selection memory to maintain continuity
- Sandbox lifecycle: Begin/end sandbox sessions around chain execution; avoid nested sessions unless explicitly managed

**Section sources**
- [core.py:117-131](file://LoOper/player/multi_sequence/worflow_interpreter_modules/executor_modules/core.py#L117-L131)
- [sequence_executor.py:1203-1221](file://LoOper/player/multi_sequence/sequence_executor.py#L1203-L1221)
- [conditional_fallback_handler.py:116-137](file://LoOper/player/multi_sequence/conditional_fallback_handler.py#L116-L137)

## Conclusion
The multi-sequence player provides a robust framework for orchestrating complex automation workflows. Its modular design separates concerns across orchestration, execution, conditional logic, and fallback handling. By leveraging caching, memory sharing, and sandbox management, it achieves reliable and efficient execution of multi-sequence chains. Proper use of conditional triggers, fallback mechanisms, and LLM integration enables sophisticated AI-assisted automation with strong error recovery and performance characteristics.