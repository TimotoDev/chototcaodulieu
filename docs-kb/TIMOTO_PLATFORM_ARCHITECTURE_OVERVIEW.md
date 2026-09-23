# TiMoto AI Platform: Comprehensive Architecture & End-to-End System Specifications

> **Target Audience:** Engineering, Product Architecture, Operations & Leadership teams  
> **Reading Time:** ~7–10 minutes  
> **Scope:** Complete 6-Repository Ecosystem Architecture, Data Topologies, Lifecycle State Machines, and Core Protocols

---

## 1. Executive Summary & Platform Mission

**TiMoto AI** is an AI-first automotive liquidity and marketplace infrastructure platform designed to modernize the fragmented two-wheeler (motorbike) market in Southeast Asia. 

The platform bridges consumer supply with institutional B2B demand by combining:
1. **Automated Computer Vision & Audio AI Inspection:** Real-time motorbike registration card (Cavet) OCR, 360° exterior damage detection, engine acoustic anomaly detection, and Standard Condition Document (SCD) report generation.
2. **Algorithmic Dual-Pricing Engine:** Instant pricing resolution balancing **Fair Market Price (FMP)** for peer-to-peer consignment and **TiMoto Instant Purchase Price (TIPP)** for immediate liquidity.
3. **End-to-End Field Operations & Digital Notary:** Dispatch orchestration for field technicians, digital on-site contract execution (e-Notary), and automated bank account validation.
4. **B2B Dealer Marketplace & Escrow Liquidity:** Real-time auctioning, catalog buy-now checkout, dynamic **VietQR Escrow deposit locking**, and automated seller payout reconciliation.

---

## 2. Global System Architecture (The 5-Pillar Ecosystem)

The architecture is divided into five specialized sub-systems that interact via authenticated REST APIs, secure WebSockets, and asynchronous event-driven synchronizations.

```mermaid
flowchart TB
  subgraph Pillar1["1. Consumer & Demand Generation Layer"]
    direction TB
    MobileApp["📱 TiMoto Mobile App<br/><b>React Native (iOS/Android)</b><br/>• 6-Step Sell Journey<br/>• Live Cavet OCR<br/>• Media Stream Upload"]
    WebLanding["🌐 TiMoto AI Web<br/><b>Next.js 14 on Vercel</b><br/>• Marketing & Waitlist<br/>• Serverless AWS Lambda API"]
  end

  subgraph Pillar2["2. Field Operations & Inspection Layer"]
    direction TB
    InternalApp["🛠️ TiMoto Internal App<br/><b>React Native (Ops Field App)</b><br/>• Technician Dispatch<br/>• 360° SCD Inspection Tool"]
    OpsEngine["⚡ Instant Sell & Notary Backend<br/><b>Django REST / PostgreSQL</b><br/>• Dual-Price Valuation<br/>• e-Notary Signing Engine<br/>• Payout Reconciliation"]
  end

  subgraph Pillar3["3. B2B Institutional Liquidity Layer"]
    direction TB
    DealerV2["🏢 Dealer Portal v2<br/><b>Next.js 14 + Tailwind CSS</b><br/>• Realtime Auction Lobby<br/>• Catalog & Bulk Buyout"]
    DealerBE["⚙️ Dealer Backend Service<br/><b>Django REST on ECS Fargate</b><br/>• NLB + Cloud Map DNS<br/>• VietQR Payment Webhooks<br/>• Cognito & Zalo OAuth"]
  end

  subgraph Pillar4["4. Core AI & Evaluation Engine"]
    direction TB
    CV_OCR["👁️ Computer Vision OCR<br/>Registration Card Recognition"]
    SCD_Model["🧠 SCD AI Analysis Engine<br/>Exterior Damage, Paint, Engine Acoustic"]
  end

  subgraph Pillar5["5. Market Intelligence & Crawling Pipeline"]
    direction TB
    Scraper["🕷️ Market Scraper Pipeline<br/><b>Python / Beautiful Soup / S3</b><br/>• Proxy Rotation & Anti-Bot<br/>• Listing Deduplication Engine<br/>• Market Price Benchmarks"]
  end

  %% Interactions
  MobileApp -->|1. Submit Meta & OCR| Pillar4
  MobileApp -->|2. Register Bike & Schedule Pickup| OpsEngine
  InternalApp -->|3. Perform 360° SCD Inspection| OpsEngine
  Scraper -->|Market Price Feed| OpsEngine
  OpsEngine -->|4. Sync Verified Inventory| DealerBE
  DealerV2 -->|5. Place Bids & Escrow Deposit| DealerBE
  DealerBE -->|6. Trigger VietQR Escrow Lock| OpsEngine
  OpsEngine -->|7. Disburse Automated Payout| MobileApp
```

---

## 3. End-to-End Vehicle Lifecycle (4-Phase Execution Model)

```mermaid
sequenceDiagram
  autonumber
  actor Rider as 👤 Consumer Seller (Rider)
  participant TMA as 📱 Mobile App (TMA)
  participant AI as 🧠 AI Evaluation Engine
  participant Ops as 🛠️ Technician (Internal App)
  participant IA_BE as ⚙️ Internal Ops Backend
  participant Dealer as 🏢 Dealer Portal v2
  participant Escrow as 🏦 VietQR / Bank Gateway

  %% PHASE 1
  rect rgb(240, 248, 255)
    Note over Rider, TMA: PHASE 1: CONSUMER INGESTION & AI VALUATION
    Rider->>TMA: Scan Cavet (Registration Card)
    TMA->>AI: Image Stream -> OCR Processing
    AI-->>TMA: Parsed Brand, Model, Year, Plate Number
    Rider->>TMA: Record 360° Photos, Video & Engine Audio
    TMA->>AI: POST /bikes/:id/evaluate/async/
    AI-->>TMA: Return SCD Diagnostic & Dual-Price Range (FMP vs TIPP)
    Rider->>TMA: Select Pricing Strategy & Book Pickup Schedule
  end

  %% PHASE 2
  rect rgb(255, 250, 240)
    Note over Rider, IA_BE: PHASE 2: ON-SITE INSPECTION & CONTRACT SIGNING
    Ops->>Rider: Technician arrives at physical location
    Ops->>Ops: Execute physical 360° SCD Checklist on Internal App
    Ops->>IA_BE: Submit Inspection Verdict (Pass/Adjustment)
    IA_BE->>Rider: Present finalized Binding Offer (TIPP)
    Rider->>Ops: Accept Offer -> Execute Digital e-Notary Agreement
    Ops->>IA_BE: Lock physical bike into TiMoto Custody
  end

  %% PHASE 3
  rect rgb(245, 255, 245)
    Note over IA_BE, Dealer: PHASE 3: B2B DEALER AUCTION & ESCROW LOCK
    IA_BE->>Dealer: Publish verified bike to B2B Catalog / Live Auction
    Dealer->>Dealer: Salon reviews SCD Inspection & Places Winning Bid
    Dealer->>Escrow: Scan Dynamic VietQR to deposit 10% Escrow
    Escrow-->>Dealer: Webhook Callback -> Escrow Confirmed & Locked
  end

  %% PHASE 4
  rect rgb(255, 240, 245)
    Note over Dealer, Escrow: PHASE 4: PHYSICAL HANDOVER & SELLER PAYOUT
    Dealer->>Ops: Final Settlement Payment & Physical Pickup
    Ops->>IA_BE: Confirm Delivery & Original Cavet Handover
    IA_BE->>Escrow: Dispatch Instant Payout to Seller Bank Account
    Escrow-->>Rider: Funds Settled in Seller Account (< 60 seconds)
  end
```

---

## 4. Repository & Codebase Matrix

The platform is maintained across 6 specialized Git repositories:

| Repository Name | Primary Tech Stack | Infrastructure / Deployment | Core Responsibilities |
| :--- | :--- | :--- | :--- |
| **`timoto-mobile`** | React Native, TypeScript, Redux Toolkit, Django REST | AWS ECS Fargate, Aurora PostgreSQL, S3 Media Bucket | • Consumer sell flow (6 stages).<br/>• Realtime OCR scanning.<br/>• Audio/Video upload pipelines.<br/>• Zalo & Apple Social Authentication.<br/>• Customer pickup scheduling. |
| **`timoto-internal-app`** | React Native (Field App), Django REST, PostgreSQL | AWS ECS Fargate, S3, FCM Notifications | • Technician on-site dispatch.<br/>• 360° SCD inspection workflows.<br/>• Instant Sell & Dual-Pricing calculation engine.<br/>• Electronic Notary contract generation.<br/>• Bank account verification & Payout engine. |
| **`timoto_dealer_portal_v2`** | Next.js 14 (App Router), Tailwind CSS, TypeScript | AWS ECS Fargate Frontend (Nginx), Route53, NLB | • Modern dealer frontend.<br/>• Live bidding rooms & catalog listings.<br/>• Dynamic VietQR modal generator.<br/>• Dealer KYC & financial history dashboard. |
| **`timoto-dealer-portal`** | Django 5 REST Framework, Celery, Redis | AWS ECS Fargate (x86_64), Cloud Map DNS (`srv-mltilck3366moqxh`) | • Dealer order lifecycle state machine.<br/>• VietQR payment webhook parser & reconciler.<br/>• AWS Cognito OTP & Zalo OAuth backend.<br/>• OneSignal push notification broadcaster. |
| **`TiMoto-AI-Web`** | Next.js 14, Tailwind CSS, AWS Lambda | Vercel Global Edge Network, AWS Lambda (Waitlist) | • Public brand presence and marketing landing.<br/>• Serverless consumer waitlist intake engine. |
| **`chototcaodulieu`** | Python 3.12, BeautifulSoup4, Requests, S3 | AWS EC2 / Cron Worker, Rotating Proxy Network | • Large-scale market data scraping (Chợ Tốt).<br/>• Data deduplication algorithms.<br/>• Historical price modeling & depreciation curve generator. |

---

## 5. Architectural Deep Dives & Protocols

### 5.1 Algorithmic Dual-Pricing Protocol (FMP vs. TIPP)

When a vehicle is ingested, the system calculates two distinct financial paths:

$$\text{FMP} = \text{Base Market Price} \times (1 - \text{Depreciation}_{\text{Age}}) - \text{SCD Damage Penalty}$$

$$\text{TIPP} = \text{FMP} \times (1 - \text{Holding Cost Margin} - \text{Liquidity Discount}) - \text{Immediate Reconditioning Cost}$$

```mermaid
graph LR
  Input[Raw Vehicle Data + SCD Report] --> Engine{Dual-Pricing Engine}
  Engine -->|Consignment Path| FMP["FMP (Fair Market Price)<br/>Higher Return (100%), Longer Time-to-Cash (7-14 Days)"]
  Engine -->|Instant Liquidity Path| TIPP["TIPP (Instant Cash Price)<br/>Immediate Settlement (24 Hours), Discounted for Liquidity"]
```

- **FMP (Fair Market Price):** Targeted for peer-to-peer buyers looking for market parity. The vehicle is listed on the public catalog while remaining in seller custody until matched.
- **TIPP (TiMoto Instant Purchase Price):** Guaranteed cash offer where TiMoto purchases the bike directly onto its balance sheet for B2B wholesale distribution.

---

### 5.2 SCD (Standard Condition Document) 3-Layer Inspection

The inspection engine enforces a strict 3-tier validation hierarchy before any vehicle can be listed or purchased:

```mermaid
graph TD
  subgraph Layer1["Layer 1: Exterior Condition (Vision AI)"]
    L1A[Paint Quality & Scratch Analysis]
    L1B[Fairing Alignment & Plastic Welds]
    L1C[Tire Tread Depth & Rim Deformation]
  end

  subgraph Layer2["Layer 2: Mechanical & Structural (Acoustic + Tech)"]
    L2A[Chassis Frame Straightness & Rust]
    L2B[Engine Acoustic Spectrogram Anomaly Check]
    L2C[Brake Fluid & Electrical Wire Harness]
  end

  subgraph Layer3["Layer 3: Legal & Valuation (OCR + Database)"]
    L3A[Chassis & Engine Number Stamping Match]
    L3B[Traffic Violation & Stolen Police Registry Sync]
    L3C[Final Liquidity & Depreciation Classification]
  end

  Layer1 --> Layer2 --> Layer3
```

---

### 5.3 VietQR Dynamic Escrow & Webhook Reconcile Engine

To eliminate payment friction and counterparty default risk in B2B auctions, TiMoto implements an automated VietQR Escrow state machine:

```mermaid
stateDiagram-v2
  [*] --> OrderCreated: Dealer Clicks 'Deposit / Buy Now'
  OrderCreated --> QRGenerated: Backend emits dynamic VietQR with unique Transaction Code
  QRGenerated --> WebhookReceived: Dealer scans QR via Banking App (NAPAS 247)
  WebhookReceived --> Reconciling: Webhook payload validated with HMAC signature
  Reconciling --> EscrowLocked: Exact amount match confirmed (< 3s)
  EscrowLocked --> InAuction: Deposit unlocks live bidding capabilities
  EscrowLocked --> FinalSettlement: Auction won -> Remaining 90% balance requested
  FinalSettlement --> PayoutTriggered: Vehicle physically transferred
  PayoutTriggered --> [*]: Funds wired to seller account
```

---

## 6. Infrastructure & Deployment Topology

```
                         [ Internet Traffic ]
                                  │
                   ┌──────────────┴──────────────┐
                   ▼                             ▼
       [ dealer.timoto.ai ]             [ api.timoto.ai ]
                   │                             │
                   ▼                             ▼
         Route53 (Hosted Zone)         AWS API Gateway / ALB
                   │                             │
                   ▼                             ▼
        Network Load Balancer (NLB)     Mobile Backend (Django)
                   │                             │
                   ▼                             ▼
         Nginx Frontend (ECS)           AWS Aurora PostgreSQL
                   │                             │
    Cloud Map DNS (dealer.local:8000)            │
                   │                             │
                   ▼                             ▼
        Dealer Backend (ECS Fargate) <─── S2S Internal Auth
                   │
                   ▼
         PostgreSQL (RDS Multi-AZ)
```

### Key Production Infrastructure Specifications:
- **AWS Region:** `ap-southeast-7` (Primary Production).
- **Compute Cluster:** AWS ECS Fargate (`timoto-dealer-fargate`, `timoto-mobile-prod`).
- **Internal Service Discovery:** AWS Cloud Map (`backend.timoto-dealer-portal.local:8000`).
- **Zero-Downtime Deployment Protocol:** Rolling task definition replacement with automatic stale target group deregistration and DNS resolver refresh (`resolver 169.254.169.253 valid=10s`).
- **Data Persistence:** AWS Aurora PostgreSQL Serverless Multi-AZ clusters with automated point-in-time recovery.

---

## 7. Engineering Best Practices & Guidelines

1. **API Contracts as Single Source of Truth:** All cross-team modifications must update `docs/CONTRACT.md` before implementation.
2. **Idempotent Webhook Processing:** All financial transactions (VietQR, Payouts) must implement strict deduplication tokens and HMAC verification.
3. **Fail-Safe S2S Fallbacks:** Remote internal API calls (such as user bank verification) must handle transient network drops gracefully without breaking the primary user experience.
4. **Clean Room Data Synchronization:** Scraper data pipelines run in isolated execution sandboxes with proxy rotation to protect production data integrity.
