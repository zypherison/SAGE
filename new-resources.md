# External resources — relevance review (2026-11-13)

A list of 22 links was supplied with the instruction: decide whether they are relevant, and
**change nothing in this iteration** because of them. This document is that decision record. No
code, configuration or claim in `sage2/` was altered on the strength of these links.

## How each link was judged

Read as: *does this give SAGE something it cannot already do — a data source, a documented API, a
reproducible method, or a citation that changes how we describe the work?* A link that is only an
index page, a login wall, or an unreadable article is marked as such rather than guessed at.

| # | Link | What it actually is | Relevant to SAGE? |
|---|---|---|---|
| 1 | `link.springer.com/subjects/geographical-information-system` | Springer's GIS subject hub — a browsable index of journals/books. | **No.** A directory, not a resource. |
| 2 | `link.springer.com/search?query=gis&...` | A filtered Springer search result page. | **No.** Search results, and the query is not scoped to our problem. |
| 3 | `10.1007/s44163-025-00811-x` | Springer article (Discover-family DOI prefix, 2025). | **Unverified.** Springer returned HTTP 406 to our fetcher; the abstract could not be read, so no judgement is made. |
| 4 | `10.1007/s13369-026-11742-9` | Springer article, *Arabian Journal for Science and Engineering* (2026). | **Unverified** — HTTP 406, as above. |
| 5 | `10.1007/s00477-026-03391-z` | Springer article, *Stochastic Environmental Research and Risk Assessment* (2026). | **Unverified** — HTTP 406, as above. |
| 6 | `sciencedirect.com/.../S3050740526000139` | ScienceDirect article page. | **Unverified** — HTTP 403. |
| 7 | same URL `#bbib0018` | The bibliography entry anchor inside #6. | **No.** An anchor, not a separate source. |
| 8 | `academic.oup.com/nsr/article/8/9/nwab027` | *National Science Review* 8(9), 2021 article. | **Unverified** — HTTP 403 (publisher login wall on the full text). |
| 9 | `earthdata.nasa.gov/.../access/eos-data-cloud` | NASA ACCESS 2017 project page: cloud JupyterHub/Pangeo tooling for EOSDIS data. | **Marginal.** Same argument as our §1 problem statement (data too large to move; analyse near the data), but it is 2017 infrastructure work, not a method we can cite for a measurement. Usable as context, not evidence. |
| 10 | `earthdata.nasa.gov/` | NASA's Earthdata portal (search/download of NASA products). | **Marginal.** Requires a free Earthdata Login. Could supply Landsat/EMIT products for deeper thermal work, but SAGE's no-login rule means it cannot be the default path. |
| 11 | `earthsystemdatalab.net/#services` | ESA Earth System Data Lab — analysis-ready Earth-system cubes and hosted services. | **Marginal.** The "analysis-ready cube" idea is exactly what our median-composite gap needs, but the platform is a research environment, not an API SAGE's browser client can call. The page itself renders client-side and returned no readable text. |
| 12 | `earthsystemdatalab.net/earthcode/` | EarthCODE (ESA): publishing workflows and data so results are findable and **reproducible**. | **Relevant, conceptually.** This is the same argument as SAGE's evidence id + replay: a result should be re-executable. Worth one citation in the integrity section; no code impact. |
| 13 | `earthsystemdatalab.net/guide/visualisation/xcube-viewer/` | xcube viewer docs — viewing data cubes in a browser. | **No, for now.** A different visualisation stack (Python/JS cube viewer) from our Leaflet map; adopting it would be a rewrite, not an improvement. |
| 14 | `earthsystemdatalab.net/data/` | Data catalogue for the same lab. | **Marginal.** Cube-shaped products (e.g. climatologies) are not what SAGE's per-acquisition AOI statistics consume. |
| 15 | `sentinel-hub.com/develop/api/` | Sentinel Hub API docs: Process, **Statistical**, Batch, Catalog APIs. Loaded successfully. | **Relevant as an upgrade path.** Its Statistical API returns "histograms, percentile calculations" server-side — i.e. it could replace the histogram we currently borrow from ArcGIS and, unlike that service, supports multi-year archives and per-pixel cloud masking. It needs OAuth client credentials, so it cannot be the keyless default but is the honest answer to "how would you fix the 14-month window and the cloud problem?" |
| 16 | `github.com/mohammadhashemii/awesome-agentic-AI-for-Geospatial-Data` | Curated list of agentic-AI-for-geospatial papers (survey by Hashemi & Züfle, 2026). Loaded successfully. | **Relevant for related work only.** It confirms the crowded space of LLM agents that *do* geospatial analysis — the axis SAGE deliberately rejects (AI parses language, never produces a number). Useful for positioning and citations; must not be read as evidence for our design. |
| 17 | `tandfonline.com/doi/full/10.1080/17538947.2026.2633849` | *International Journal of Digital Earth* article (2026). | **Unverified** — HTTP 403. |
| 18 | same URL `?needAccess=true` | Paywall route to the same article. | **No.** A paywall query string, not a separate resource. |
| 19 | `skywatch.com` | Commercial imagery marketplace/API ("one platform" across providers, AI data advisor). Loaded. | **No for the prototype.** Paid, account-based, and aimed at sourcing imagery rather than measuring an AOI. Relevant only as a "who would pay for this" comparison in the business section. |
| 20 | `bhuvan-app3.nrsc.gov.in/data/download/...` | ISRO/NRSC **NOEDA** open EO data archive (Cartosat, LISS, AWiFS, DEM). Loaded, but the requested dataset filter matched nothing. | **Marginal and India-specific.** Genuinely useful resolution (Cartosat-1 2.5 m) for an Indian AOI, but it is a login/download portal with no statistics API, so it breaks both the no-login and the deterministic-number rules. A possible future "download path", not an analysis source. |
| 21 | `bhuvan-app1.nrsc.gov.in/2dresources/bhuvanstore.php` | Bhuvan Store — paid/ordered NRSC products. | **No.** Ordering portal; outside a free, keyless prototype. |
| 22 | `link.springer.com/search?...content-type=...` variants | (Same as #2; duplicates in the supplied list.) | **No.** |

## Honest summary

- **Nothing here changes the current iteration.** The three tiers delivered today (class areas,
  same-month comparison, Landsat brightness temperature) were designed from live probes of the
  services already in use, not from this list.
- **One link is a real technical lead:** Sentinel Hub's Statistical API (#15). It is the only
  source in the list that documents server-side histograms *plus* a multi-year archive *plus*
  cloud masking, which are precisely the three limits SAGE now states openly. If the project ever
  gets a credential budget, that is the first integration to evaluate — as an **additional**
  provider behind the same interface, with the current keyless path kept as the default.
- **Two links are citation material:** EarthCODE (#12) for the reproducibility argument behind
  evidence/replay, and the agentic-geospatial survey list (#16) for related work.
- **Seven article links could not be read** (HTTP 403/406 from Springer, ScienceDirect, OUP and
  Taylor & Francis). They are recorded as unverified rather than summarised from their titles.
  Any future use must start by actually reading the abstract and methods.
- **The rest are portals, indexes, anchors or paywalls.** They are not datasets and do not supply
  a method; listing them as supporting evidence in the seminar document would inflate the
  reference list without adding an argument.

## If this list is revisited

1. Read #3–#5, #6, #8 and #17 through the institution's library access (the fetcher is blocked;
   a browser session is not), then re-judge them on methods rather than titles.
2. Evaluate Sentinel Hub Statistical API on the Hussain Sagar AOI: same histogram, same dates,
   compare against the ArcGIS numbers already recorded — and check whether its cloud mask removes
   the −12 °C contaminated scenes that forced the plausibility filter in `thermal.py`.
3. Only after (1) and (2) decide whether any of it belongs in the report, and mark clearly which
   claims are measured and which are cited.
