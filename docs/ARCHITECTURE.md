# System Architecture (MVP)

![Architecture](architecture.png)

## Components

```mermaid
flowchart LR
    subgraph Browser
        UI[Call page + Dashboard<br/>HTML/JS]
        VAD[Silero VAD<br/>ONNX in browser]
    end
    subgraph Backend["FastAPI backend (Python)"]
        API[REST API<br/>app/main.py]
        AG[Agent orchestrator<br/>app/agent.py]
        TOOLS[Tools<br/>app/tools.py]
        AN[Post-call analysis<br/>app/analysis.py]
        RAG[RAG search<br/>app/rag.py]
        DB[(SQLite<br/>leads, calls, turns,<br/>appointments, kb_chunks)]
    end
    subgraph External["External APIs (free tiers)"]
        STT[Sarvam Saaras<br/>speech-to-text]
        TTS[Sarvam Bulbul<br/>text-to-speech]
        LLM[Gemini Flash-Lite<br/>+ function calling]
        EMB[Gemini embeddings]
        HS[HubSpot CRM<br/>optional]
        CAL[Cal.com<br/>optional]
    end
    UI --> VAD
    VAD -- WAV of one utterance --> API
    API --> AG
    AG --> STT
    AG --> LLM
    AG --> TTS
    LLM -- tool calls --> TOOLS
    TOOLS --> RAG --> EMB
    TOOLS --> DB
    TOOLS --> CAL
    API --> AN --> LLM
    AN --> HS
    RAG --> DB
    AG --> DB
```

## One conversation turn

```mermaid
sequenceDiagram
    participant C as Caller (browser)
    participant V as VAD (browser)
    participant B as FastAPI
    participant S as Sarvam STT/TTS
    participant G as Gemini
    participant T as Tools
    C->>V: speaks
    V->>V: detects end of speech (800 ms silence)
    V->>B: POST /api/calls/{id}/turn (16 kHz WAV)
    B->>S: speech-to-text (language auto-detect)
    S-->>B: transcript + language code
    B->>B: choose reply language
    B->>G: chat.send_message (history + tools)
    G->>T: e.g. update_qualification / search_knowledge_base
    T-->>G: result
    G-->>B: reply text
    B->>S: text-to-speech in caller's language
    S-->>B: WAV (base64)
    B-->>C: transcript, reply, audio, lead score
    Note over C,V: If the caller talks while audio plays -> playback stops (barge-in)
```

## Call lifecycle

1. **Start** (`POST /api/calls/start`): find-or-create lead by phone -> build system prompt with
   **long-term memory** (BANT so far, last 3 call summaries, existing bookings) -> Gemini greets.
2. **Turns** (`/turn` or `/text`): STT -> LLM with tools -> TTS. Chat history = **short-term memory**.
3. **End** (`POST /api/calls/{id}/end`): Gemini returns structured JSON (summary, sentiment, trend,
   objections, buying signals, next step, outcome) -> saved -> CRM sync (local + HubSpot).
4. **Dashboard**: KPIs, objections, language mix, leads, appointments, transcripts, AI insights.

## Key design decisions (and why)

| Decision | Why |
|---|---|
| Cascaded STT -> LLM -> TTS (not speech-to-speech) | Brief asks for STT/TTS; text at every step feeds CRM, sentiment, summaries; provider per stage can be swapped |
| VAD in the browser | No audio streaming server needed for the MVP; only finished utterances are sent, so STT cost = speech time only |
| Sarvam for speech | Built for Indian languages and code-mixed Hinglish; free credits |
| Gemini function calling for actions | The LLM decides *when* to search/qualify/book; tools enforce rules (valid criterion, slot really free) |
| Deterministic BANT scoring in Python | Explainable, testable, same input -> same score |
| SQLite + numpy vector search | Zero setup for the MVP; same schema moves to PostgreSQL + pgvector |
| Local fallbacks for CRM and calendar | Demo never depends on optional third-party accounts |
| Tools never raise exceptions | They return error text so the LLM can recover ("slot taken -> offer another") |

## Data model

```mermaid
erDiagram
    LEADS ||--o{ CALLS : has
    CALLS ||--o{ TURNS : contains
    LEADS ||--o{ APPOINTMENTS : books
    CALLS ||--o{ APPOINTMENTS : "booked during"
    LEADS {
        int id PK
        string name
        string phone UK
        string email
        string bant_json
        int score
        string status
        string hubspot_contact_id
    }
    CALLS {
        int id PK
        int lead_id FK
        string language
        int duration_sec
        string summary
        string sentiment
        string outcome
        string analysis_json
    }
    TURNS {
        int id PK
        int call_id FK
        string role
        string text
        string language
    }
    APPOINTMENTS {
        int id PK
        int lead_id FK
        string start_time
        string source
        string external_id
    }
    KB_CHUNKS {
        int id PK
        string source
        string heading
        string content
        string embedding_json
    }
```
