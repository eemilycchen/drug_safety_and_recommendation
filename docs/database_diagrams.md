# Database Diagrams — Drug Safety & Recommendation

Diagrams for the four databases used in the project. View this file in a Markdown viewer that supports Mermaid (e.g. GitHub, VS Code with Mermaid extension), or open `database_diagrams.html` in a browser.

---

## 1. PostgreSQL (Relational) — Synthea patient data

**Role:** Patient state — demographics, encounters, active medications, conditions, allergies, observations. The schema supports both current-state queries (`get_patient_profile`, `get_active_medications`) and history/timeline queries (`get_medication_history`, `get_patient_timeline`) for richer context in MongoDB and the vector DB.

**Data source:** Synthea CSV exports.

Star schema: `patients` at the center; clinical events reference `patients` and often `encounters`.

```mermaid
erDiagram
    organizations ||--o{ providers : "employs"
    patients ||--o{ encounters : "has"
    encounters }o--o| organizations : "at"
    encounters }o--o| providers : "with"
    encounters }o--o| payers : "payer"
    patients ||--o{ medications : "has"
    encounters ||--o{ medications : "at"
    patients ||--o{ conditions : "has"
    encounters ||--o{ conditions : "at"
    patients ||--o{ allergies : "has"
    encounters ||--o{ allergies : "at"
    patients ||--o{ observations : "has"
    encounters ||--o{ procedures : "at"
    patients ||--o{ procedures : "has"
    patients ||--o{ immunizations : "has"
    patients ||--o{ careplans : "has"
    patients ||--o{ payer_transitions : "has"

    organizations {
        uuid id PK
        text name
        text address
        text city
        text state
    }

    payers {
        uuid id PK
        text name
    }

    providers {
        uuid id PK
        uuid organization FK
        text name
        text speciality
    }

    patients {
        uuid id PK
        date birthdate
        date deathdate
        text first_name
        text last_name
        text gender
        text address
        text city
        text state
    }

    encounters {
        uuid id PK
        timestamptz start_ts
        timestamptz stop_ts
        uuid patient FK
        uuid organization FK
        uuid provider FK
        uuid payer FK
        text encounterclass
        text description
    }

    medications {
        serial id PK
        timestamptz start_ts
        timestamptz stop_ts
        uuid patient FK
        uuid encounter FK
        text code
        text description
    }

    conditions {
        serial id PK
        date start_date
        date stop_date
        uuid patient FK
        uuid encounter FK
        text code
        text description
    }

    allergies {
        serial id PK
        date start_date
        date stop_date
        uuid patient FK
        uuid encounter FK
        text code
        text description
    }

    observations {
        serial id PK
        timestamptz obs_date
        uuid patient FK
        uuid encounter FK
        text code
        text value
    }
```

---

## 2. Neo4j (Graph) — Drug interactions & side effects

**Role:** Drug–drug interactions and drug–side-effect relationships; graph traversals for safety checks.

**Data sources:** RxNav (interactions), SIDER (side effects).

**Nodes:** `Drug`, `SideEffect`. **Relationships:** `INTERACTS_WITH` (Drug–Drug), `HAS_SIDE_EFFECT` (Drug→SideEffect).

```mermaid
flowchart LR
    subgraph nodes["Node types"]
        D1[(Drug)]
        D2[(Drug)]
        SE[(SideEffect)]
    end

    subgraph relationships["Relationships"]
        D1 <-.->|INTERACTS_WITH<br/>severity, description| D2
        D1 -->|HAS_SIDE_EFFECT<br/>frequency| SE
    end
```

**Conceptual graph (example):**

```mermaid
flowchart LR
    A[Aspirin<br/>Drug]
    B[Warfarin<br/>Drug]
    C[Metformin<br/>Drug]
    S1[Nausea<br/>SideEffect]
    S2[Bleeding<br/>SideEffect]

    A <-->|INTERACTS_WITH| B
    B -->|HAS_SIDE_EFFECT| S1
    B -->|HAS_SIDE_EFFECT| S2
    C -->|HAS_SIDE_EFFECT| S1
```

---

## 3. Qdrant (Vector) — Similar adverse events

**Role:** Similarity search over embedded adverse event reports (and optionally patient summaries).

**Data source:** openFDA FAERS (often via normalized docs from MongoDB).

**Structure:** One or more collections; each point = vector (embedding) + payload (e.g. report id, drug names).

```mermaid
flowchart TB
    subgraph Qdrant["Qdrant collection(s)"]
        direction TB
        P1["Point 1: vector (embedding) + payload (faers_id, drugs, ...)"]
        P2["Point 2: vector + payload"]
        P3["Point 3: vector + payload"]
        P1 --- P2
        P2 --- P3
    end

    subgraph Input["Input"]
        Q["Query vector (patient + drug summary)"]
    end

    Q -->|similarity search<br/>e.g. top_k nearest| Qdrant
    Qdrant -->|returns| R["Similar FAERS report IDs + scores"]
```

**Key idea:** Embeddings of FAERS report text (and/or patient context); query by vector to find “similar” real-world adverse events.

---

## 4. MongoDB (Document) — Evidence & audit

**Role:** Store raw FAERS documents, normalized summaries for embedding/lookup, and an audit log of each safety check.

**Collections:** Three main collections in database `drug_safety`.

```mermaid
flowchart TB
    subgraph MongoDB["MongoDB database: drug_safety"]
        subgraph faers_raw["faers_raw"]
            R1["Doc: raw openFDA API response"]
            R2["Doc: raw openFDA API response"]
            R1 --- R2
        end

        subgraph faers_normalized["faers_normalized"]
            N1["Doc: _id, summary, drugs[], reactions[], receivedate, ..."]
            N2["Doc: same structure"]
            N1 --- N2
        end

        subgraph safety_check_audit["safety_check_audit"]
            A1["Doc: run_id, patient_id, proposed_drug, inputs, outputs, timestamp"]
            A2["Doc: run record"]
            A1 --- A2
        end
    end

    faers_raw -.->|same _id = safetyreportid| faers_normalized
```

**Collection summary:**

| Collection            | Purpose |
|-----------------------|--------|
| `faers_raw`           | Raw JSON from openFDA Drug Event API; `_id` = safetyreportid. Traceability. |
| `faers_normalized`    | Flattened doc (summary, drugs, reactions, dates) for embedding and evidence display; same `_id` for lookup. |
| `safety_check_audit`  | One document per safety-check run: inputs, outputs, timestamp (audit trail). |

---

## All four in one view

```mermaid
flowchart LR
    subgraph PG["PostgreSQL"]
        P[patients]
        E[encounters]
        M[medications]
        P --- E
        P --- M
    end

    subgraph N4["Neo4j"]
        D[(Drug)]
        S[(SideEffect)]
        D --- S
    end

    subgraph Q["Qdrant"]
        V[Vectors]
    end

    subgraph MON["MongoDB"]
        F[faers_raw / faers_normalized]
        A[safety_check_audit]
    end

    PG --> N4
    N4 --> Q
    Q --> MON
```
