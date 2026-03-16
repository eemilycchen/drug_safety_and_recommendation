# FAERS Report Structure (openFDA drug/event)

`data/faers_sample_pretty.json` contains **adverse event reports** from the FDA Adverse Event Reporting System (FAERS). Each report is one “case”: a patient, the drug(s) they took, and the reaction(s) that were reported. openFDA serves this as JSON from the **drug/event** API.

---

## What one report is

One JSON object = **one adverse event report**: “Someone (patient) took one or more drugs and had one or more reactions; a reporter sent this to the FDA.”

---

## Top-level fields (report header)

| Field | Meaning | Example |
|-------|--------|--------|
| **safetyreportid** | Unique report ID | `"5801206-7"`, `"10003300"` |
| **receivedate** | When FDA received the report (YYYYMMDD) | `"20080707"` |
| **receiptdate** | When the report was received (YYYYMMDD) | `"20080625"` |
| **transmissiondate** | When transmitted to FDA (YYYYMMDD) | `"20090109"` |
| **serious** | FDA: `"1"` = serious, `"0"` or missing = non-serious. In Qdrant we derive **serious** from the same outcome flags (death, hospitalization, life-threatening, disabling) so that "non-serious" and serious: no always match. | `"1"` |
| **seriousnessdeath** | `"1"` if death was reported | `"1"` |
| **seriousnesshospitalization** | `"1"` if hospitalization | `"1"` |
| **seriousnesslifethreatening** | `"1"` if life-threatening | `"1"` |
| **seriousnessdisabling** | `"1"` if disabling | `"1"` |
| **seriousnesscongenitalanomaly** | `"1"` if congenital anomaly | |
| **primarysource** | Who originally reported | `{"reportercountry": "US", "qualification": "5"}` |
| **primarysourcecountry** | Country of primary reporter | `"US"`, `"CA"` |
| **reporttype** | Type of report (e.g. `"1"` = expedited) | `"1"` |
| **duplicate** | `"1"` if this is a duplicate of another report | `"1"` |
| **reportduplicate** | Source and number of duplicate | `{"duplicatesource": "GENENTECH", "duplicatenumb": "1289378"}` |
| **sender** / **receiver** | FDA routing metadata | Usually `"FDA-Public Use"` / `"FDA"` |

Date format codes (e.g. **receivedateformat**: `"102"`) mean **YYYYMMDD** (ISO 8601 calendar date).

---

## `patient` object

Everything about the patient and the event: demographics, drugs, and reactions.

### Demographics

| Field | Meaning | Example / codes |
|-------|--------|------------------|
| **patientonsetage** | Age (number as string) | `"26"`, `"77"` |
| **patientonsetageunit** | Unit for age | `"801"` = years, `"802"` = months, `"803"` = weeks, `"804"` = days, `"800"` = decade |
| **patientsex** | Sex | `"1"` = male, `"2"` = female |
| **patientweight** | Weight (when provided) | e.g. `"81"` |
| **patientdeath** | Death date if applicable | `{"patientdeathdate": null}` or a date |

### `patient.drug[]` — drugs (suspect or concomitant)

Each element is one drug in the report.

| Field | Meaning | Example |
|-------|--------|--------|
| **medicinalproduct** | Drug name as reported (brand or generic) | `"DURAGESIC-100"`, `"BONIVA"`, `"IBUPROFEN"` |
| **drugcharacterization** | Role of drug | `"1"` = suspect, `"2"` = concomitant, `"3"` = interacting, etc. |
| **drugindication** | Indication (why the drug was used) | `"DRUG ABUSE"`, `"OSTEOPOROSIS"`, `"PULMONARY HYPERTENSION"` |
| **drugadministrationroute** | Route (code) | `"041"` = oral, `"042"` = IV, etc. |
| **drugdosagetext** | Dosage as text | `"3 MG, 1 IN 3 M, INTRAVENOUS (NOT OTHERWISE SPECIFIED)"` |
| **drugstartdate** / **drugenddate** | Start/end of use (YYYYMMDD) | `"20130913"` |
| **drugauthorizationnumb** | Application number (FDA) | `"021858"` |
| **drugbatchnumb** | Batch number | `"H6200HO3"` |
| **openfda** | **Optional.** Enriched FDA data when the product is matched | See below |

### `patient.reaction[]` — adverse reactions

Each element is one reaction term (MedDRA preferred term).

| Field | Meaning | Example |
|-------|--------|--------|
| **reactionmeddrapt** | MedDRA preferred term (standardized reaction) | `"OVERDOSE"`, `"Vomiting"`, `"Drug ineffective"` |
| **reactionmeddraversionpt** | MedDRA version | `"17.0"` |
| **reactionoutcome** | Outcome of this reaction (code) | e.g. `"4"` |

---

## `openfda` (inside a drug)

When the reported product can be matched to FDA’s reference data, openFDA adds an **openfda** block to that drug. Not every report has it (e.g. old or non-standard names may not match).

| Field | Meaning | Example |
|-------|--------|--------|
| **generic_name** | Generic name(s) | `["IBUPROFEN"]` |
| **brand_name** | Brand name(s) | `["IBUPROFEN", "MOTRIN", ...]` |
| **substance_name** | Active substance | `["IBUPROFEN"]` |
| **pharm_class_epc** | Established Pharmacologic Class | `["Nonsteroidal Anti-inflammatory Drug [EPC]"]` |
| **pharm_class_moa** | Mechanism of action | `["Cyclooxygenase Inhibitors [MoA]"]` |
| **application_number** | NDA/ANDA etc. | `["ANDA078682", "NDA020944", ...]` |
| **product_ndc** | NDC product codes | List of NDC numbers |
| **rxcui** | RxNorm concept ID | Used for interoperability |

Your ETL uses **openfda.generic_name** (or **medicinalproduct** when openfda is missing) as the drug name for Qdrant.

---

## How your project uses this

- **Parsing** (`etl/load_faers_to_qdrant.parse_report`): Reads `patient.drug[]` and `patient.reaction[]`, pulls drug names from `openfda.generic_name` or `medicinalproduct`, and builds one structured record per report.
- **Serialization**: That record is turned into a short text (e.g. “Patient: 26 year old male. Medications: … Reactions: …”) and embedded with BioLORD.
- **Qdrant**: Each report becomes one vector in the **adverse_events** collection, with payload such as `drug`, `reactions`, `patient_age`, `patient_sex`, `serious`, `outcome`, `raw_text`.

---

## Summary

- **One report** = one patient + list of **drugs** + list of **reactions** + seriousness/outcome and metadata.
- **patient.drug[]** = each drug (name, indication, route, dates, optionally **openfda**).
- **patient.reaction[]** = each reaction (MedDRA term).
- **openfda** = added by openFDA when the product is recognized; used for generic name and (in other pipelines) pharmacologic class.

For full code lists (e.g. route, outcome), see [openFDA drug event documentation](https://open.fda.gov/apis/drug/event/) and FDA’s ICSR (E2B) implementation guide.
