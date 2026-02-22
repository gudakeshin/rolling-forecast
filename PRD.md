# UC 05 — Rolling Forecast Generation & Refresh

## Product Requirements Document (PRD)

**Status:** Engineering-Ready Spec
**Owner:** FP&A Product Team
**Last Updated:** February 2026
**Stack:** Agnostic (designed to work with any ERP, EPM, data warehouse, or spreadsheet backend)

---

## 1. Problem Statement

FP&A teams produce forecasts on a monthly or quarterly cadence, but the process is almost entirely manual: analysts pull historical actuals from ERP or data warehouse, paste into spreadsheet models, adjust driver assumptions one line at a time, rebalance interdependencies, and reconcile totals — typically taking 5–10 business days per cycle.

The result is that forecasts are stale before they're published. By the time a monthly forecast clears review, the underlying data is already 2–3 weeks old. Worse, the manual process introduces silent errors — wrong copy-paste ranges, stale assumption cells, broken formula links — that erode trust in forecast accuracy over time.

**Who experiences this:** FP&A analysts (build forecasts), finance directors (review and approve), business unit heads (provide driver assumptions), CFOs (consume forecasts for decisions), and investors/board (rely on guidance accuracy).

**Cost of not solving:**

| Impact Area | Current Pain |
|-------------|-------------|
| Cycle time | 5–10 business days per forecast refresh |
| Staleness | Forecast is 2–3 weeks old by publication |
| Accuracy | ±10–15% variance to actuals on volatile P&L lines |
| Analyst time | 60–70% of FP&A bandwidth consumed by mechanical forecast work |
| Error rate | ~25% of forecast submissions contain formula or data-pull errors |
| Strategic impact | Leadership makes decisions on outdated numbers; re-forecasting for ad-hoc requests takes days |

---

## 2. Goals

| # | Goal | Measurement |
|---|------|-------------|
| G1 | Reduce forecast refresh cycle from 5–10 days to < 1 day through automated statistical baselines and driver input workflows | Median days from cycle kickoff to published forecast |
| G2 | Improve forecast accuracy by blending statistical models with human judgment, targeting < 5% variance on top-line revenue | MAPE (mean absolute percentage error) vs. actuals at 1-quarter horizon |
| G3 | Surface model confidence explicitly so analysts focus review effort on low-confidence lines rather than reviewing everything equally | % of analyst review time spent on flagged low-confidence items |
| G4 | Support rolling 12–18 month horizon with monthly granularity, replacing static annual budgets with continuously updated views | Forecast horizon coverage (months forward) and refresh frequency |
| G5 | Maintain full assumption auditability — every forecast number traceable to either a statistical model output or a human override | 100% of forecast values have source attribution in audit trail |

---

## 3. Non-Goals (V1)

| # | Non-Goal | Rationale |
|---|----------|-----------|
| NG1 | Replacing the budgeting process entirely | Rolling forecasts supplement, not replace, the annual budget. Budget process has its own governance and approval workflows. |
| NG2 | Demand planning / operational forecasting | V1 focuses on financial P&L forecasting. Supply chain, inventory, and demand forecasts are a separate domain with different data sources and models. |
| NG3 | Automated forecast approval and publication | All forecasts require human review and explicit approval before publication. No autonomous distribution to stakeholders. |
| NG4 | Custom model training by end users | V1 uses pre-configured statistical models (ARIMA, Prophet, etc.). Users can override outputs but cannot train custom ML models through the UI. |
| NG5 | Real-time streaming forecast updates | V1 operates on batch cadence (daily or monthly refresh). Sub-daily or event-driven forecast updates are P2. |
| NG6 | Balance sheet and working capital forecasting | V1 covers P&L lines (revenue, COGS, OpEx). Balance sheet and cash flow forecasting are covered by UC 08 (Cash Flow Forecasting). |

---

## 4. User Stories

### 4.1 FP&A Analyst

**US-01:** As an FP&A analyst, I want the agent to generate a statistical baseline forecast for all P&L lines using historical actuals, so I only need to review and adjust where business context differs from historical patterns.

**Acceptance Criteria:**
- Given historical actuals for 24+ months are available
- When the analyst triggers a forecast refresh
- Then the system generates a baseline forecast for each P&L line item using the best-fit statistical model
- And each line shows: forecasted value, model type used, confidence interval (P10/P50/P90), and historical fit score (R² or MAPE)
- And lines with confidence < configurable threshold are visually flagged as "Needs Review"

**US-02:** As an FP&A analyst, I want to override any forecasted value with my own estimate and provide a reason, so the forecast reflects business context the model can't capture (e.g., a known contract signing, a planned product launch).

**Acceptance Criteria:**
- Given a generated baseline forecast
- When the analyst selects a line item and enters an override value
- Then the system accepts the override, records the reason text, and recalculates all dependent line items (e.g., if revenue is overridden, gross margin recalculates)
- And the override is tagged with analyst name, timestamp, and reason
- And the original model output is preserved alongside the override for comparison
- And a "Revert to Model" option is available for each overridden line

**US-03:** As an FP&A analyst, I want to see model accuracy tracking over time — how well the agent's forecasts performed vs. actuals in prior periods — so I can calibrate my trust in the baseline and know which lines need more attention.

**Acceptance Criteria:**
- Given 3+ completed forecast cycles with actuals available
- When the analyst views accuracy tracking
- Then display: MAPE by line item for last 3/6/12 months, trend direction (improving/declining), and comparison to analyst-only forecast accuracy (if baseline exists)
- And lines are sortable by accuracy — worst performers at top

**US-04:** As an FP&A analyst, I want the agent to automatically detect and handle seasonality, trends, and structural breaks in historical data, so I don't need to manually adjust models for known patterns.

**Acceptance Criteria:**
- Given historical data with seasonal patterns (e.g., Q4 revenue spike)
- When the forecast is generated
- Then the model correctly captures seasonal components and does not extrapolate anomalous periods (e.g., COVID quarter) without flagging them
- And structural breaks (e.g., acquisition, product discontinuation) are detected; model uses post-break data unless analyst overrides
- And seasonality detection is visible to the analyst: "Detected 12-month seasonal cycle with peak in Q4"

---

### 4.2 Finance Director

**US-05:** As a finance director, I want a forecast review dashboard that highlights only the lines where (a) the model has low confidence, (b) variance to prior forecast is material, or (c) an analyst has flagged for attention, so I can review efficiently without examining every line.

**Acceptance Criteria:**
- Given a completed baseline forecast with analyst overrides applied
- When the director opens the review dashboard
- Then lines are categorized: "Auto-Approved" (high confidence, low variance, no flags), "Needs Review" (low confidence or material variance), "Analyst Flagged" (manually flagged items)
- And "Needs Review" items show: forecasted value, confidence interval, variance to prior forecast (absolute and %), and model fit score
- And the director can approve, reject, or send back individual lines with comments

**US-06:** As a finance director, I want to compare the current forecast to prior forecast, budget, and actuals in a single view, so I can understand the forecast trajectory and explain changes to leadership.

**Acceptance Criteria:**
- Given current forecast, prior forecast (at least 1), annual budget, and actuals (YTD)
- When comparison view is opened
- Then display a table with columns: Line Item, YTD Actuals, Current Forecast, Prior Forecast, Budget, Variance (Current vs. Prior), Variance (Current vs. Budget)
- And variances are color-coded: green (favorable), red (unfavorable), with configurable materiality thresholds
- And drill-down on any variance shows the driver breakdown (volume, price, mix, FX, timing where applicable)

---

### 4.3 Business Unit Head

**US-07:** As a business unit head, I want to provide my revenue and cost driver assumptions through a simple input form rather than editing a spreadsheet model, so I can contribute to the forecast without needing to understand the model structure.

**Acceptance Criteria:**
- Given it is forecast input collection period
- When the BU head receives the input request
- Then they see a form with pre-populated fields showing prior period values and model-suggested values for their BU-specific drivers (e.g., unit volume, average selling price, headcount, marketing spend)
- And they can accept suggested values or override with their own estimate plus a reason
- And validation rules fire in real time: e.g., "Revenue growth of 50% exceeds historical range — please confirm or adjust"
- And submission triggers automatic recalculation of the BU's P&L forecast lines

**US-08:** As a business unit head, I want to see how my driver inputs translate into P&L impact in real time, so I understand the financial implications of my assumptions before submitting.

**Acceptance Criteria:**
- Given the BU head is entering driver assumptions
- When a value is changed
- Then the P&L preview updates within 3 seconds showing the impact on revenue, COGS, gross margin, OpEx, and EBITDA for their BU
- And changes are highlighted vs. the prior forecast baseline

---

### 4.4 CFO

**US-09:** As a CFO, I want a one-page executive forecast summary showing key metrics (revenue, EBITDA, cash flow proxy), risk-adjusted ranges, and the top 5 assumption changes from last forecast, so I can quickly assess where we stand.

**Acceptance Criteria:**
- Given a published forecast
- When the CFO opens the executive summary
- Then display: key metrics with P10/P50/P90 ranges, a bridge chart showing top 5 drivers of change from prior forecast, forecast confidence score (weighted average of line-level confidence), and a 2–3 sentence AI-generated narrative summary
- And exportable to PowerPoint in one click

**US-10:** As a CFO, I want the ability to request an ad-hoc forecast refresh with updated actuals mid-cycle, so I can make time-sensitive decisions with the freshest possible numbers.

**Acceptance Criteria:**
- Given new actuals are available (e.g., flash revenue numbers)
- When the CFO requests an ad-hoc refresh
- Then the system re-runs the statistical models with updated actuals, preserves all existing analyst overrides that are still valid, flags overrides that may need revision (e.g., override was for a period that now has actuals), and produces an updated forecast within 4 hours
- And the ad-hoc refresh is logged as a distinct forecast version (not overwriting the official cycle forecast)

---

### 4.5 All Roles — Context & External Intelligence

**US-11A:** As any forecast stakeholder, I want to upload internal documents (board decks, strategy reports, market research, contracts) so the AI agent can reference them when answering my questions or providing forecast context.

**Acceptance Criteria:**
- Given the user has an internal document relevant to forecasting
- When they upload it via the chat input or Document Library panel
- Then the system parses, chunks, and indexes the document for semantic search
- And the agent automatically retrieves relevant passages when the user asks related questions
- And the user can search across all their uploaded documents from the Document Library

**US-11B:** As an FP&A analyst or CFO, I want the agent to access live market data, economic indicators, and web search when I ask about external factors, so I can incorporate current market context into forecast assumptions without leaving the platform.

**Acceptance Criteria:**
- Given the user asks about stock prices, CPI, interest rates, or market news
- When the agent processes the query
- Then it calls the appropriate data tool (yfinance for stocks, FRED for economic data, Perplexity for web search)
- And returns structured data with source attribution and citations
- And results are formatted for easy comprehension (charts, key metrics, trend data)

**US-11C:** As a user, I want to share a URL with the agent and have it read and remember the content, so I can reference external articles and reports in my forecast discussions.

**Acceptance Criteria:**
- Given the user pastes a URL in the chat
- When the agent processes it
- Then it fetches and extracts the readable content
- And optionally indexes it in the document library for future reference
- And provides a summary of the page content in the chat

---

### 4.6 Data / Platform Engineering

**US-12:** As a data engineer, I want forecast models to consume actuals via a standardized data API (not file uploads), so actuals ingestion is automated and auditable.

**Acceptance Criteria:**
- Given a configured data source connection
- When a forecast refresh is triggered
- Then the system pulls actuals from the registered data source via API
- And data freshness is validated: if actuals are older than expected (e.g., missing most recent month), the system flags this before proceeding
- And data lineage is recorded: source system, extraction timestamp, row counts, and any transformations applied

**US-13:** As a platform engineer, I want forecast generation to be idempotent — re-running the same forecast with the same inputs produces the same outputs — so we can debug and reproduce any forecast.

**Acceptance Criteria:**
- Given a forecast run with specific inputs (actuals version, model version, parameters)
- When the same run is re-executed
- Then outputs are bit-for-bit identical
- And model versions are pinned per run (no "latest" references)
- And random seeds are fixed and logged for stochastic models

---

## 5. Requirements

### 5.1 Must-Have (P0)

#### P0-01: Historical Actuals Ingestion

Automated pull of historical financial actuals from the configured data source with validation and lineage tracking.

**Acceptance Criteria:**
- [ ] Connects to data source via standardized API (REST or SQL)
- [ ] Pulls GL-level actuals with dimensions: account, cost center, BU, geography, product, period
- [ ] Validates data completeness: flags missing periods, missing accounts, or unexpected zero balances
- [ ] Validates data freshness: warns if most recent period is older than expected
- [ ] Records data lineage: source, timestamp, row count, hash of extracted data
- [ ] Supports incremental pull (only new/changed periods) for efficiency
- [ ] Handles currency: stores in original currency with conversion rates for reporting currency

**Technical Notes:** Design the ingestion layer as a pluggable adapter pattern — each data source (ERP, warehouse, EPM) implements a standard interface. V1 needs at least one adapter; architecture must support adding more without core changes.

---

#### P0-02: Statistical Baseline Forecast Engine

Generate statistical forecast for each P&L line item using historical actuals, with model selection, confidence scoring, and seasonality handling.

**Acceptance Criteria:**
- [ ] Supports at minimum: ARIMA/SARIMA, Prophet, exponential smoothing (ETS), and linear trend
- [ ] Auto-selects best model per line item based on historical fit (lowest MAPE on holdout set)
- [ ] Produces point forecast (P50) and confidence interval (P10/P90) per line per period
- [ ] Detects and handles seasonality automatically (monthly, quarterly patterns)
- [ ] Detects structural breaks and adjusts model window accordingly (configurable: use post-break data only, or flag for analyst decision)
- [ ] Handles sparse data gracefully: lines with < 12 months of history use simpler models (trend/average) with wider confidence intervals
- [ ] Forecast horizon: configurable 12–18 months forward, monthly granularity
- [ ] Model metadata stored per line: model type, parameters, training window, fit metrics (MAPE, R², AIC)
- [ ] Entire forecast generation completes within 30 minutes for a standard chart of accounts (< 2,000 line items)

**Technical Notes:** Consider using a model registry pattern where each model type is a pluggable component. Auto-selection should use time-series cross-validation (walk-forward) rather than simple train/test split. Prophet handles seasonality well but is slow at scale — consider parallel execution.

---

#### P0-03: Confidence Scoring & Review Prioritization

Score each forecast line by model confidence and surface low-confidence items for analyst review.

**Acceptance Criteria:**
- [ ] Confidence score per line item on a 0–100 scale based on: model fit (MAPE), prediction interval width, data quality (completeness, recency), and historical volatility
- [ ] Configurable threshold for "Needs Review" (default: confidence < 70)
- [ ] Lines categorized as: "High Confidence" (auto-approvable), "Medium Confidence" (review recommended), "Low Confidence" (review required)
- [ ] Review queue presents low-confidence items first, sorted by materiality (absolute dollar impact)
- [ ] Each item in the review queue shows: forecasted value, confidence score, confidence drivers (which factors are dragging it down), historical context chart, and comparison to prior forecast
- [ ] Analysts cannot publish a forecast with unreviewed "Low Confidence" items unless a director overrides

---

#### P0-04: Human Override & Assumption Management

Allow analysts to override any model-generated forecast value with their own estimate, with reason tracking and dependency recalculation.

**Acceptance Criteria:**
- [ ] Override any line item for any future period with a manual value
- [ ] Require reason text for each override (free text, minimum 10 characters)
- [ ] Recalculate all dependent line items when an override is applied (e.g., override revenue → recalculate gross margin, tax provision)
- [ ] Dependency graph is configurable: define which lines drive which downstream calculations
- [ ] Preserve original model output alongside override for comparison ("Model said X, analyst overrode to Y because Z")
- [ ] "Revert to Model" option per line, per period, or bulk revert
- [ ] Override history maintained: who, when, what value, what reason, for each change
- [ ] Overrides carry forward to next cycle by default (configurable: persist until actuals available, or expire after N cycles)

**Technical Notes:** The dependency graph is critical and complex. Consider a DAG (directed acyclic graph) representation where nodes are line items and edges are calculation dependencies. When an override is applied, traverse downstream nodes and recalculate. Must detect and prevent circular dependencies.

---

#### P0-05: Business Driver Input Collection

Structured input forms for business unit heads to provide driver assumptions, with validation and real-time P&L impact preview.

**Acceptance Criteria:**
- [ ] Configurable input forms per BU/cost center with relevant driver fields (e.g., volume, ASP, headcount, marketing spend)
- [ ] Pre-populated with: prior period actuals, model-suggested values, and prior forecast values
- [ ] Real-time P&L impact preview: changes to any driver update the P&L summary within 3 seconds
- [ ] Validation rules: range checks (historical min/max), cross-field consistency (e.g., headcount × avg salary ≈ payroll), growth rate alerts
- [ ] Submission workflow: BU head submits → FP&A analyst reviews → analyst approves or sends back with comments
- [ ] Reminder notifications for overdue submissions (configurable cadence)
- [ ] Input deadline enforcement: soft deadline (warning) and hard deadline (auto-populate with model values)

---

#### P0-06: Forecast Comparison & Variance Tracking

Compare current forecast to prior forecasts, budget, and actuals with variance analysis.

**Acceptance Criteria:**
- [ ] Comparison columns: YTD Actuals, Current Forecast, Prior Forecast (N-1), Budget, and deltas
- [ ] Variance calculated as absolute dollars and percentage
- [ ] Color-coded: favorable (green), unfavorable (red), with configurable materiality thresholds
- [ ] Drill-down on any variance shows driver breakdown where driver data is available
- [ ] Bridge/waterfall chart showing what changed between current and prior forecast (top N drivers)
- [ ] Supports comparison at any aggregation level: total company, BU, geography, product line
- [ ] Historical forecast accuracy tracking: MAPE of each past forecast vs. actuals, trended over time

---

#### P0-07: Forecast Versioning & Audit Trail

Maintain complete version history of every forecast with full lineage from inputs to outputs.

**Acceptance Criteria:**
- [ ] Each forecast cycle produces a versioned, immutable snapshot
- [ ] Version record includes: actuals data version (hash), model versions used, all parameters, all overrides, all driver inputs, reviewer approvals, and final output
- [ ] Any historical version can be re-loaded for comparison or re-run
- [ ] Audit log exportable as CSV/JSON for SOX documentation
- [ ] Retention: minimum 3 years (configurable per org)
- [ ] Version naming: auto-generated (e.g., "FC-2026-03-v1") with optional custom label
- [ ] Diff view between any two forecast versions showing what changed

---

#### P0-08: Role-Based Access & Approval Workflow

Control who can generate, edit, review, approve, and publish forecasts.

**Acceptance Criteria:**
- [ ] Roles: Input Provider (submit driver assumptions), Analyst (generate + override + submit for review), Reviewer (approve/reject line items), Publisher (release to stakeholders), Admin (configure models, mappings, roles)
- [ ] Forecast publication requires Reviewer approval
- [ ] Low-confidence items cannot bypass review (unless Admin override)
- [ ] SSO integration for authentication; RBAC for authorization
- [ ] All actions logged with user, timestamp, and action type
- [ ] Configurable approval workflow: single-level or multi-level (analyst → director → CFO)

---

### 5.2 Nice-to-Have (P1)

#### P1-01: Ensemble Model Selection

Instead of picking one best model per line, run multiple models and blend outputs for improved accuracy.

**Acceptance Criteria:**
- [ ] Run top 3 models per line item; weight outputs by inverse MAPE
- [ ] Ensemble output shown alongside individual model outputs
- [ ] Analyst can switch between ensemble and single-model view
- [ ] Accuracy tracking compares ensemble vs. single-model over time

---

#### P1-02: Auto-Generated Forecast Commentary

Generate narrative commentary explaining the forecast and key changes from prior period.

**Acceptance Criteria:**
- [ ] 2–3 paragraph summary per BU and for total company
- [ ] Covers: key metric movements, top 5 drivers of change from prior forecast, risk factors (low-confidence lines), and assumption changes
- [ ] Clearly labeled as "AI-generated draft — review required"
- [ ] Editable before publication; edit history tracked
- [ ] Exportable for insertion into board decks and management reports

---

#### P1-03: What-If Branching from Forecast

Allow analysts to create scenario branches from the current forecast baseline without modifying the official forecast.

**Acceptance Criteria:**
- [ ] "Branch" action creates a copy of current forecast state
- [ ] Analyst can modify any parameters on the branch independently
- [ ] Branch outputs viewable side-by-side with base forecast
- [ ] Branches can be promoted to official forecast (with approval) or archived
- [ ] Links to UC 07 (Scenario Modeling) for natural language scenario input on branches

---

#### P1-04: Automated Accuracy Reporting

Monthly automated report comparing prior forecasts to actual results with trend analysis.

**Acceptance Criteria:**
- [ ] Auto-generated accuracy report when new actuals are loaded
- [ ] MAPE by line item, BU, and total company
- [ ] Trend chart: accuracy over last 6–12 months
- [ ] Highlights: most improved lines, most deteriorated lines, persistent bias (consistently over/under-forecasting)
- [ ] Distributed to FP&A team and finance leadership automatically

---

#### P1-05: Anomaly Detection on Actuals

Flag anomalous actuals during ingestion that may distort forecast models.

**Acceptance Criteria:**
- [ ] Statistical anomaly detection on incoming actuals (z-score, IQR-based)
- [ ] Flagged anomalies shown to analyst with options: exclude from model training, include with dampening, or include as-is
- [ ] Links to UC 03 (KPI Monitoring) for ongoing anomaly detection

---

#### P1-06: Context Engine & Document Intelligence

Provide a context engine that allows users to upload documents (PDF, DOCX, PPTX, XLSX, CSV, TXT), ingest web URLs, and search across uploaded content using semantic search (RAG). Integrate live web search, financial data APIs, and economic indicators to enrich forecast context with external intelligence.

**Acceptance Criteria:**
- [ ] Users can upload documents in all common formats (PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML) via drag-and-drop or file picker
- [ ] Uploaded documents are automatically parsed, chunked, embedded, and indexed in a local vector store (ChromaDB) for semantic search
- [ ] Users can ingest content from any URL — the system fetches, extracts text, and indexes it alongside uploaded documents
- [ ] Semantic search across all uploaded documents returns ranked passages with source attribution and relevance scores
- [ ] The AI agent automatically retrieves relevant context from uploaded documents when answering user questions (auto-RAG)
- [ ] Live web search via Perplexity Sonar API provides grounded, cited answers for market news, competitor analysis, and regulatory updates
- [ ] Financial data lookup via yfinance (stock prices, company financials) and FRED API (GDP, CPI, unemployment, interest rates) is available as an agent skill
- [ ] URL fetch skill allows the agent to read and optionally index any web page the user references
- [ ] Document Library panel provides a management UI: upload, browse, filter by scope/type, search, and delete documents
- [ ] Document scope controls visibility: "My Documents" (user-scoped), "This Chat" (conversation-scoped), or "Global" (org-wide)
- [ ] All 4 context tools (search_context, web_search, fetch_url, financial_lookup) are registered as agent skills and available via natural language

**Technical Notes:**
- Document processing pipeline uses LangChain document loaders (PyPDFLoader, Docx2txtLoader, CSVLoader, WebBaseLoader) for parsing and RecursiveCharacterTextSplitter for chunking (1500 chars, 200 overlap).
- Embeddings generated locally using sentence-transformers (all-MiniLM-L6-v2, 384-dim) — no external API calls for embedding, zero incremental cost.
- ChromaDB in persistent mode stores vectors locally with metadata filtering by user, document, scope, and file type.
- Perplexity Sonar API (sonar/sonar-pro models) provides synthesized web search answers with inline citations rather than raw links.
- FRED API provides economic indicators (GDP, CPI, unemployment, Fed funds rate, Treasury yields). yfinance provides stock prices, market data, and company financials (income statements, balance sheets, cash flow). No API key needed for yfinance; FRED requires a free key.
- Auto-retrieval injects top-3 relevant document chunks into the MasterAgent system prompt per query, giving the agent passive context awareness without explicit tool calls.

---

### 5.3 Future Considerations (P2)

| # | Feature | Design Implication |
|---|---------|-------------------|
| P2-01 | **ML-Based Forecast Models** — Train custom models (XGBoost, LSTM) on org-specific data for higher accuracy on complex lines | Design model registry to support arbitrary model types via a standard predict() interface, not just classical time-series |
| P2-02 | **External Driver Integration** — Incorporate macro-economic indicators (GDP, CPI, interest rates), industry data, or weather data as model features | Build data ingestion layer to accept external data sources with configurable join keys (period, geography). Note: P1-06 Context Engine now provides FRED API and yfinance integration as agent skills, laying the groundwork for this. |
| P2-03 | **Real-Time Streaming Updates** — Update forecast continuously as new data arrives (daily sales, weekly bookings) rather than batch monthly | Design forecast engine as a stateless computation that can be triggered by data events, not just manual/scheduled runs |
| P2-04 | **Balance Sheet & Cash Flow Extension** — Extend beyond P&L to forecast balance sheet line items and full cash flow statement | Keep the model registry and override system generic enough to handle any financial statement line, not just P&L |
| P2-05 | **Natural Language Forecast Queries** — Allow users to ask "Why did the forecast change?" or "What's driving the revenue increase?" in natural language | Store forecast metadata in a queryable format; consider building a semantic layer over forecast data. Note: P1-06 Context Engine's search_context skill and auto-RAG provide a foundation for this. |

---

## 6. Success Metrics

### 6.1 Leading Indicators (Days–Weeks)

| Metric | Target | Stretch | Measurement Method |
|--------|--------|---------|-------------------|
| Forecast cycle time | < 1 day (from kickoff to draft) | < 4 hours | Calendar days from data refresh trigger to analyst-ready draft |
| Baseline generation time | < 30 min for full P&L | < 10 min | Wall-clock time for statistical model execution |
| Driver input collection time | 80% of BUs submit within 2 days | Within 1 day | Time from input request to submission per BU |
| Confidence flag accuracy | > 85% of flagged items actually need changes | > 90% | % of "Needs Review" items where analyst made a material override |
| Adoption rate | 80% of FP&A team using within 60 days | 95% | Unique users who complete at least 1 forecast cycle via the system |
| Override rate | 20–40% of line items overridden | — | Too high = model is poor; too low = analysts aren't reviewing. Track trend. |

### 6.2 Lagging Indicators (Weeks–Months)

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Forecast accuracy (revenue) | MAPE < 5% at 1-quarter horizon | Agent forecast vs. actuals, compared to pre-agent baseline |
| Forecast accuracy (EBITDA) | MAPE < 8% at 1-quarter horizon | Same methodology |
| Accuracy improvement over time | Model MAPE decreases quarter-over-quarter | Trend line on accuracy metrics |
| Analyst time reallocation | 50% reduction in time spent on mechanical forecast work | Time-tracking survey: before vs. after |
| Forecast freshness | Average forecast age < 1 week at any point | Days since last published forecast at time of any decision request |
| Stakeholder trust | Finance leadership confidence score > 4/5 | Quarterly survey of forecast consumers |

### 6.3 Evaluation Cadence

- **Week 1:** System performance (generation time, data pull reliability)
- **Month 1:** Adoption + cycle time + override patterns
- **Quarter 1:** Full accuracy assessment (first cycle with actuals to compare)
- **Ongoing quarterly:** All metrics with trend analysis and model retraining assessment

---

## 7. Open Questions

### Blocking (must resolve before development starts)

| # | Question | Owner | Context |
|---|----------|-------|---------|
| OQ-01 | What is the minimum historical data requirement? | Data Eng + Data Science | Statistical models need sufficient history. 24 months is ideal, but some entities may have less. What is the fallback? |
| OQ-02 | How are P&L line item dependencies defined? | FP&A + Engineering | The override recalculation requires a dependency graph. Who defines and maintains it? Is it extracted from existing models or manually configured? |
| OQ-03 | What constitutes the "chart of accounts" for forecasting? | FP&A | Forecast at GL account level? Summary level? How many line items per BU? This determines model count and compute requirements. |
| OQ-04 | Data source connector for V1? | Engineering + IT | Which system provides actuals first? (ERP, data warehouse, EPM?) This determines the first adapter to build. |

### Non-Blocking (can resolve during implementation)

| # | Question | Owner | Context |
|---|----------|-------|---------|
| OQ-05 | Should model selection be fully automatic or analyst-overridable per line? | Product + FP&A | Some analysts may prefer specific models for certain lines. How much control do we expose? |
| OQ-06 | Forecast approval workflow: single-tier or multi-tier? | FP&A Leadership | Depends on org structure. Configurable is safest, but adds complexity. |
| OQ-07 | How to handle newly created cost centers with no history? | FP&A + Data Science | No historical data = no statistical model. Use peer averages? Manual-only? Proxy from similar entities? |
| OQ-08 | Currency handling: forecast in local currency and convert, or forecast in reporting currency? | FP&A + Treasury | Impacts model accuracy (FX noise) and override workflow. |
| OQ-09 | Integration with UC 07 Scenario Modeling? | Product | Should scenario branches live in the same system, or connect via API? Sharing model definitions would be efficient. |

---

## 8. Timeline & Phasing

### Phase 1: Data Foundation & Baseline Engine (Weeks 1–8)

- Actuals ingestion adapter (P0-01) — build first connector
- Statistical forecast engine (P0-02) — core model framework + 4 model types
- Confidence scoring (P0-03) — scoring algorithm and review prioritization
- Forecast versioning (P0-07) — version storage and audit trail
- **Milestone:** System ingests actuals, generates statistical baseline for full P&L, produces confidence scores. No human override yet.

### Phase 2: Human Workflow & Overrides (Weeks 9–14)

- Override system with dependency recalculation (P0-04)
- Driver input collection forms (P0-05)
- Forecast comparison view (P0-06)
- RBAC and approval workflow (P0-08)
- **Milestone:** Full end-to-end forecast cycle possible: ingest → generate → collect inputs → override → review → approve. Internal beta with FP&A team.

### Phase 3: Hardening & Launch (Weeks 15–18)

- Performance optimization (30-min generation target)
- Error handling for all edge cases (sparse data, missing periods, circular dependencies)
- Security review and penetration testing
- SOX audit trail validation with Internal Audit
- User acceptance testing with FP&A team
- **Milestone:** Production launch for first forecast cycle.

### Phase 4: P1 Features — Analytics & Intelligence (Weeks 19–26)

- Ensemble models (P1-01)
- Auto-generated commentary (P1-02)
- What-if branching (P1-03)
- Automated accuracy reporting (P1-04)
- Anomaly detection on actuals (P1-05)
- **Milestone:** Enhanced feature set live. Measure accuracy improvement from ensemble models.

### Phase 5: Context Engine & External Intelligence (Weeks 27–30)

- Context engine with document upload and RAG search (P1-06)
  - Document processing pipeline: parse (PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, URL) → chunk → embed → store in ChromaDB
  - Semantic search over uploaded documents with source attribution
  - Auto-retrieval: agent automatically pulls relevant context from documents per query
- Web search via Perplexity Sonar API with synthesized answers and citations
- Financial data lookup: yfinance (stock prices, company financials) + FRED API (economic indicators)
- URL fetch and indexing skill
- Document Library panel (upload, browse, filter, search, delete)
- Enhanced chat input supporting all document types
- CFO/Executive dashboard enhancements with business insights and forward-looking analysis
- Anomaly dashboard with AI-prioritized, filterable, actionable anomalies
- **Milestone:** Context-aware agent with external intelligence. Users can upload internal documents and query live market/economic data within the forecast workflow.

### Dependencies

| Dependency | Owner | Needed By |
|------------|-------|-----------|
| Actuals data source API access | IT / Data Eng | Phase 1 start |
| Chart of accounts definition for forecasting | FP&A | Phase 1 (model configuration) |
| P&L dependency graph (line item relationships) | FP&A + Engineering | Phase 2 (override recalculation) |
| SSO / identity provider integration | Platform | Phase 2 (RBAC) |
| SOX audit trail requirements | Internal Audit | Phase 3 (validation) |
| Corporate report templates | FP&A + Design | Phase 2 (comparison views) |
| Perplexity API key | Engineering | Phase 5 (web search skill) |
| FRED API key (free) | Engineering | Phase 5 (economic indicators) |

---

## 9. Architecture Notes (for engineering context)

### High-Level Component Map

```
┌──────────────────────────────────────────────────────────────┐
│                     User Interface Layer                      │
│  Analyst Dashboard │ BU Input Forms │ Director Review │ CFO  │
└──────────┬─────────┴───────┬────────┴────────┬───────┴──────┘
           │                 │                  │
  ┌────────▼────────┐  ┌────▼──────────┐  ┌───▼──────────────┐
  │ Override Engine   │  │ Input Collection│  │ Comparison Engine │
  │ (dependency DAG)  │  │ (forms + valid.)│  │ (variance calc)   │
  └────────┬─────────┘  └────┬──────────┘  └───┬──────────────┘
           │                 │                  │
  ┌────────▼─────────────────▼──────────────────▼──────────────┐
  │                  Forecast Orchestrator                       │
  │  Sequences: Ingest → Generate → Collect → Override → Review │
  └────────────────────────┬───────────────────────────────────┘
                           │
  ┌────────────────────────▼───────────────────────────────────┐
  │               Statistical Model Engine                      │
  │  Model Registry │ Auto-Selection │ Confidence Scoring       │
  │  ARIMA │ Prophet │ ETS │ Linear │ [P1: Ensemble │ P2: ML]  │
  └────────────────────────┬───────────────────────────────────┘
                           │
  ┌────────────────────────▼───────────────────────────────────┐
  │                Data Ingestion Layer                          │
  │  Adapter Pattern: ERP Adapter │ Warehouse Adapter │ ...     │
  │  Validation │ Lineage │ Freshness Checks                   │
  └────────────────────────┬───────────────────────────────────┘
                           │
  ┌────────────────────────▼───────────────────────────────────┐
  │              Audit Trail & Version Store                     │
  │  Immutable log │ Forecast snapshots │ SOX-ready export      │
  └────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Adapter pattern for data sources.** Each data source implements a standard `IActualsProvider` interface. The core system never knows which specific ERP or warehouse it's talking to. This enables stack-agnostic deployment.

2. **Model registry is pluggable.** Each statistical model implements a standard interface: `fit(history) → parameters` and `predict(parameters, horizon) → forecast + confidence`. New model types can be added without changing the orchestrator or UI.

3. **Dependency graph for overrides.** P&L line item relationships are represented as a DAG. When an override is applied, the system traverses the graph downstream and recalculates affected lines. This must be acyclic — the system should validate on configuration and reject circular references.

4. **Forecast snapshots are immutable.** Once a forecast version is created, it is never modified. Overrides create new versions. This ensures reproducibility and audit compliance.

5. **Confidence scoring is composite.** The score blends multiple signals (model fit, data quality, volatility, prediction interval width) into a single 0–100 score. The weighting of these signals should be configurable by FP&A admins.

6. **Idempotent execution.** Same inputs (actuals version + model version + parameters) must always produce the same outputs. Random seeds are fixed and logged. No "latest" model references in production runs.

---

## 10. Error States & Edge Cases

| Scenario | Expected Behavior |
|----------|------------------|
| Line item has < 12 months of history | Use simpler model (linear trend or average). Set confidence to "Low" automatically. Flag for analyst review. |
| Line item has all zeros in history | Skip statistical modeling. Set to zero with confidence "N/A". Flag: "No historical activity detected — manual input required if non-zero expected." |
| Actuals data pull fails or times out | Retry 3x with exponential backoff. If still fails: alert analyst, block forecast generation, display last successful actuals version with warning. |
| Actuals are missing for most recent period | Proceed with available data but flag: "Warning: most recent actuals are from [month]. Forecast baseline may be stale." |
| Circular dependency detected in P&L line item graph | Reject configuration change. Display: "Circular dependency detected between [Line A] → [Line B] → [Line A]. Please resolve before proceeding." |
| Override creates impossible value (e.g., negative revenue where not expected) | Soft warning: "Override value is outside expected range. Are you sure?" Allow override with documented acknowledgment. |
| BU head submits driver input after hard deadline | Accept submission but mark as "Late." Include late-submitted values in next forecast refresh, not current cycle. |
| Model selection produces ties (two models with identical MAPE) | Use the simpler model (fewer parameters). Log selection reason. |
| Statistical model generates negative values for a non-negative line | Floor at zero. Flag: "Model produced negative forecast for [line]. Clamped to $0 — review recommended." |
| Concurrent users overriding the same line item | Last-write-wins with full audit trail showing both overrides. Notify both users: "[User B] also modified [Line X] at [timestamp]." |
| Forecast generation exceeds 30-minute timeout | Terminate job. Alert analyst: "Forecast generation timed out. Consider reducing line item count or simplifying model configuration." Log diagnostics. |
| Structural break detected in historical data | Flag for analyst: "Structural break detected in [month] for [line]. Using post-break data for forecast. Override if incorrect." |
| Uploaded document cannot be parsed | Mark document status as "failed" with error message. Inform user: "Could not extract content from [filename]. Try a different format." |
| ChromaDB embedding fails for a document | Mark document as "failed." Preserve the raw file. Suggest re-upload or manual review. |
| Perplexity API key missing or invalid | Skill returns: "Web search is not configured. Please set PERPLEXITY_API_KEY." Agent falls back to other available tools. |
| FRED API key missing when economic data requested | Skill returns: "FRED API key not configured." Agent suggests using web search as alternative for economic data. |
| URL fetch fails (timeout, 404, blocked) | Skill returns: "Could not fetch URL: [error]." Agent suggests trying a different URL or using web search. |
| Document too large to process | Reject with size limit message. Suggest splitting the document or uploading key sections only. |