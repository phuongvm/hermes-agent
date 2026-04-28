# Research Report: Honcho Memory Service Architecture

## 1. Philosophical Foundations
Honcho distinguishes itself from traditional passive Retrieval-Augmented Generation (RAG) by championing an **active, reasoning-driven memory** approach. Its goal is not mere fact retrieval, but true "stateful simulation" achieved by constructing coherent psychological models of entities. 
- **Formal Logic vs. Plausible Text**: Honcho leverages custom language models trained in formal logic to extract latent knowledge and draw deductive conclusions rather than just surfacing similar text chunks.
- **The Peer-Centric Paradigm**: Honcho abandons the traditional "user vs. assistant" dynamic. All entities—humans, agents, or groups—are equally treated as "Peers". This allows multi-agent systems to model and reason about other agents exactly as they do human users.

```mermaid
sequenceDiagram
    participant HP as Human Peer
    participant S as Session
    participant AP as AI Agent Peer
    participant Honcho
    
    Note over HP,AP: Peer-Centric Paradigm: All entities are equal Peers
    HP->>S: Send Message
    AP->>S: Send Message
    AP->>Honcho: Query state of Human Peer
    Honcho-->>AP: Returns Peer Card & Psychological Model
```

## 2. Core Architecture
The system employs a hierarchical data model (`Workspaces -> Peers -> Sessions -> Messages`) and splits operations into a standard memory storage layer and a background Reasoning Layer. The Reasoning Layer consists of three key agents:

```mermaid
sequenceDiagram
    participant W as Workspace
    participant P as Peer
    participant S as Session
    participant M as Message
    
    W->>P: Create/Identify Peer
    P->>S: Initialize Session
    S->>M: Record Message
    M-->>Honcho: Trigger async reasoning layer
```

### The Deriver (Ingestion & Extraction)
- Acts as the immediate reasoning engine for incoming data.
- Processes messages asynchronously via a queue.
- Uses **token batching** (triggering inference roughly every 1,000 tokens) to ensure meaningful context while keeping API costs low. 
- Extracts explicit facts and derives unstated deductive insights.

```mermaid
sequenceDiagram
    participant S as Session
    participant Q as Message Queue
    participant D as The Deriver
    participant DB as Knowledge Base
    
    S->>Q: Incoming Messages
    Note over Q: Accumulate ~1,000 tokens
    Q->>D: Trigger async processing batch
    D->>D: Run Formal Logic Reasoning Pass
    D->>DB: Extract & Save Explicit Facts
    D->>DB: Deduce & Save Latent Insights
```

### The Dreamer (Consolidation)
- A background maintenance agent running every 8 to 24 hours.
- Performs a "random walk" across peer observations to consolidate memory: merging redundancies, deleting outdated or conflicting information, and synthesizing specific facts into broader patterns (induction and abduction).
- Its primary output is the **Peer Card**, a highly compressed biographical profile (hard-capped at 40 facts) that is injected into the agent's prompt to bypass retrieval latency.

```mermaid
sequenceDiagram
    participant Timer as Cron (8-24h)
    participant Dr as The Dreamer
    participant DB as Knowledge Base (Facts)
    participant PC as Peer Card
    
    Timer->>Dr: Trigger consolidation cycle
    Dr->>DB: Read "Random Walk" of Facts
    Dr->>Dr: Merge redundancies & Resolve conflicts
    Dr->>Dr: Synthesize patterns (Induction/Abduction)
    Dr->>DB: Delete outdated/conflicting facts
    Dr->>PC: Update Peer Card (Max 40 Core Facts)
```

### The Dialectic (Retrieval & Synthesis)
- A natural language retrieval API that acts as an "Oracle" for querying peer memory.
- Uses **Multi-Pass Reasoning**: Pass 0 (Assessment), Pass 1 (Self-Audit), and Pass 2 (Reconciliation of contradictions).
- Automatically toggles between "Cold Start" (broad biography) and "Warm Session" (scoped to recent context).
- Outputs a **Dialectic Supplement**—real-time LLM-synthesized reasoning about the user's current needs—which is injected alongside the base context on every conversational turn.

```mermaid
sequenceDiagram
    participant Agent
    participant Dialectic as The Dialectic
    participant DB as Knowledge Base
    
    Agent->>Dialectic: Natural Language Query
    Dialectic->>DB: Pass 0: Assess current state & Broad bio
    DB-->>Dialectic: Initial context
    Dialectic->>Dialectic: Pass 1: Self-Audit (Identify gaps)
    Dialectic->>DB: Query specific historical facts
    DB-->>Dialectic: Specific context
    Dialectic->>Dialectic: Pass 2: Reconciliation (Resolve contradictions)
    Dialectic-->>Agent: Returns 'Dialectic Supplement' (Bespoke Context)
```

## 3. Honcho MCP Tool Integrations
The Honcho Model Context Protocol (MCP) server provides agents with direct manipulation capabilities over the memory layer:

- `honcho_context`: Retrieves the full cross-session user representation (summaries, peer cards, relevant observations).
- `honcho_ask`: An LLM-powered Q&A tool leveraging the Dialectic API. Supports configurable reasoning depths (quick vs. thorough).
- `honcho_conclude`: Allows the agent to explicitly save a new insight or fact as a "conclusion".
- `honcho_profile`: Retrieves or updates the user's biographical "peer card".
- `honcho_search_conclusions`: Performs semantic search over the derived insights for high-fidelity fact recall.
- `honcho_search_messages`: Queries historical session messages (filterable by date and sender).
- `get_config` / `set_config`: Utility tools to programmatically inspect or modify memory configurations.

```mermaid
sequenceDiagram
    participant Agent
    participant MCP as Honcho MCP Server
    participant Backend as Honcho API
    
    Agent->>MCP: Call honcho_ask(query)
    MCP->>Backend: Execute Dialectic API
    Backend-->>MCP: Dialectic Supplement
    MCP-->>Agent: Return Context
    
    Agent->>MCP: Call honcho_conclude(insight)
    MCP->>Backend: Explicitly inject new Fact
    Backend-->>MCP: Success
    MCP-->>Agent: Confirm save
    
    Agent->>MCP: Call honcho_profile()
    MCP->>Backend: Fetch Peer Card
    Backend-->>MCP: Peer Card (40 facts)
    MCP-->>Agent: Return Profile
```

## 4. Visual Diagrams

### 4.1 Comprehensive Honcho Master Pipeline
This diagram visualizes how the Philosophical Foundations (Peer-Centric), Data Architecture, Active Reasoning Engines (Deriver, Dreamer, Dialectic), and the MCP Tool Integrations all connect into a single unified flow.

```mermaid
flowchart TB
    %% Styles
    classDef peer fill:#3b82f6,stroke:#1e40af,color:white,stroke-width:2px,rx:10px
    classDef mcp fill:#8b5cf6,stroke:#5b21b6,color:white,stroke-width:2px,rx:10px
    classDef storage fill:#10b981,stroke:#047857,color:white,stroke-width:2px
    classDef agent fill:#f59e0b,stroke:#b45309,color:white,stroke-width:2px,rx:10px
    classDef output fill:#ef4444,stroke:#991b1b,color:white,stroke-width:2px

    subgraph 1_Peer_Centric_Environment [1. Philosophical: Peer-Centric Environment]
        H1[Human Peer]:::peer
        A1[AI Agent Peer]:::peer
    end

    subgraph 2_Honcho_MCP_Tools [2. Honcho MCP Tool Integrations]
        direction LR
        T_Ask[honcho_ask]:::mcp
        T_Context[honcho_context <br> honcho_search_*]:::mcp
        T_Conclude[honcho_conclude]:::mcp
        T_Profile[honcho_profile]:::mcp
    end

    H1 <-->|Interacts with| A1
    A1 <-->|Executes| 2_Honcho_MCP_Tools

    subgraph 3_Honcho_Backend [3. Honcho Core Architecture & Reasoning]
        direction TB
        
        subgraph Data_Storage [Data Storage Hierarchy]
            direction LR
            WS[Workspace] --> P[Peer] --> S[Session] --> M[Messages]
        end

        subgraph Reasoning_Engine [Active Reasoning Engine]
            direction TB
            Deriver[The Deriver <br> Ingestion & Extraction]:::agent
            Dreamer[The Dreamer <br> Periodic Consolidation]:::agent
            Dialectic[The Dialectic <br> NL Retrieval Oracle]:::agent
        end
        
        M -->|Async Queue <br> ~1000 tokens| Deriver
        Deriver -->|Logic Extraction| Facts[(Derived Conclusions)]:::storage
        
        Facts -.->|8-24hr cycle <br> Random Walk| Dreamer
        Dreamer -->|Synthesize| PCard[Peer Card <br> Max 40 Core Facts]:::storage
        
        Facts --> Dialectic
        PCard --> Dialectic
    end

    %% MCP to Backend connections
    T_Ask -->|NL Query| Dialectic
    T_Context -.->|Direct Read| Facts
    T_Conclude -->|Manual Inject| Facts
    T_Profile <-->|Read / Update| PCard

    Dialectic -->|Multi-pass Reasoning <br> Assessment/Audit/Reconciliation| Supplement[Dialectic Supplement <br> Stateful Context]:::output
    Supplement -->|Injected into Context| A1
```
