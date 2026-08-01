# AAP Product Documentation Manifests (2.5, 2.6, 2.7)

## Summary

Bundle Red Hat Ansible Automation Platform (AAP) product documentation as searchable manifests in ansible-know-mcp, covering versions 2.5, 2.6, and 2.7. Users will be able to `search_docs` for AAP topics and `fetch_doc` the full page on demand.

## Motivation

The current doc manifests cover upstream community tools (ansible-core, ansible-lint, navigator, builder, creator, molecule) — all hosted on docs.ansible.com. AAP product documentation lives on docs.redhat.com and covers installation, configuration, automation mesh, execution environments, RBAC, troubleshooting, and more. Adding AAP manifests closes the gap for users working with the full platform, not just the upstream components.

## Research Findings

### Access: docs.redhat.com is public, no login required

All AAP documentation on docs.redhat.com is publicly accessible without authentication. Confirmed via `curl` — landing pages and individual guide/topic pages all return HTTP 200.

**Caveat:** docs.redhat.com uses Akamai edge caching (not Cloudflare). Requests with browser-like `User-Agent` headers get HTTP 403 (bot protection), but requests without a browser UA succeed. The existing `fetch_doc` httpx client uses our `ansible-know-mcp/{version}` UA which should work fine.

### Official Red Hat Docs MCP server EXISTS (internal)

There **is** an official internal Red Hat Docs MCP server, built by the DocX tools team:

- **Official name:** "Red Hat Docs MCP server"
- **Compass:** `https://compass.redhat.com/catalog/xe/component/docs-009-red-hat-documentation-mcp-server`
- **Purpose:** DocX team built this because docs.redhat.com uses Cloudflare to block web scraping. They want people to use the MCP server for agentic doc access and will continue blocking scrapers.
- **MCP client example:** `content-crawl-rh-docs-mcp` on internal GitLab (`gitlab.cee.redhat.com/jtbd/content-crawl-rh-docs-mcp`) — Python tool that calls the Docs MCP server to crawl guides/topics into CSV content inventories.
- **Usage:** Pass a docs.redhat.com product URL → it enumerates all guides/topics, extracts titles and sections. Demonstrated with OpenShift 4.22 (98 guides, 18,464 rows) and AI Inference 3.5.
- **Source:** Donagh Brennan (ccs-jtbd-doers channel, Jul 11 2026): *"The DocX tools team have developed a Red Hat Docs MCP server to encourage people to use this to search content agentically and will continue to block web scraping."*

**MCP endpoint:** `https://docs-mcp.api.redhat.com/mcp` — no auth required (tested while on VPN; may or may not require VPN — needs off-VPN verification). See API details section below. Note: docs.redhat.com itself does not host the endpoint — `/mcp`, `/.well-known/mcp`, `/mcp/sse` all return 302 → 404.

### Red Hat's official MCP server portfolio (May 2026 OPL)

Four official MCP servers announced in the May 2026 naming update:

| Server | Status | Details |
|--------|--------|---------|
| MCP server for Red Hat security content | Dev Preview | Live on catalog.redhat.com, CSAF/CVE data, no auth needed for public data |
| MCP server for Red Hat product information | Dev Preview | Live on catalog.redhat.com, lifecycle/SLA/support data. Docs: `docs.redhat.com/en/documentation/mcp_server_for_red_hat_product_information/1.0` |
| MCP server for Red Hat knowledge | Announced | Upcoming — likely to cover docs.redhat.com content and KCS articles |
| MCP server for Red Hat support cases | Announced | Upcoming — support case management |

**Key insight:** The "MCP server for Red Hat knowledge" is the one most relevant to our use case. When it ships, it could serve as a live backend for AAP docs search instead of (or alongside) our static manifests. Worth tracking.

### Red Hat Documentation MCP server — API details (from content-crawl-rh-docs-mcp source)

The internal MCP client at `gitlab.cee.redhat.com/jtbd/content-crawl-rh-docs-mcp` reveals the full API:

**Server endpoint:** `https://docs-mcp.api.redhat.com/mcp`
**Protocol:** MCP over Streamable HTTP (JSON-RPC), protocol version `2024-11-05`
**Authentication:** NONE — no tokens required (tested while on VPN; off-VPN access not yet verified). The `content-crawl-rh-docs-mcp` README states "No VPN needed to run", but we haven't confirmed independently.
**Dependencies:** Pure Python stdlib (`urllib.request`), zero packages

**Available tools:**
| Tool | Purpose | Response |
|------|---------|----------|
| `redhat_docs_fetch(url)` | Fetch any docs.redhat.com page | Landing pages → structured JSON; guide pages → markdown |
| `redhat_docs_search` | Search docs (mentioned in README diagram, not used by client) | Unknown — needs investigation |

**MCP session flow:**
```
1. POST /mcp  method: "initialize"
   params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: {...} }
   → response includes mcp-session-id header

2. POST /mcp  method: "notifications/initialized"  (notification, no id)

3. POST /mcp  method: "tools/call"
   params: { name: "redhat_docs_fetch", arguments: { url: "<docs.redhat.com URL>" } }
   → SSE response with text/event-stream body containing JSON-RPC result
```

**Landing page response structure** (for product landing URLs):
```json
{
  "categoryTitles": {
    "What's new": {
      "titles": [
        { "name": "New features and enhancements", "url": "https://docs.redhat.com/.../whats_new-aap_26", "description": "..." },
        ...
      ]
    },
    "Install": { "titles": [...] },
    ...
  }
}
```

**Guide page response:** Returns raw markdown content (wrapped in `{"result": "<markdown>"}` JSON envelope with escaped newlines).

**This changes the approach significantly:**
1. **Manifest building:** Use `redhat_docs_fetch` on AAP landing pages to get structured guide lists with titles, descriptions, and categories — better than scraping HTML
2. **`fetch_doc` backend for docs.redhat.com:** Use `redhat_docs_fetch` to get markdown on demand — eliminates the HTML-to-markdown conversion problem entirely
3. **`redhat_docs_search`:** If it supports keyword search, it could serve as a live search fallback for AAP docs (like RTD Search API does for upstream docs)
4. **Anti-bot proof:** Official API, won't be affected by Cloudflare blocks that the DocX team is actively tightening

### Related: mcp-redhat-knowledge (KCS API)

The community [`mcp-redhat-knowledge`](https://github.com/sleepytimeshon/mcp-redhat-knowledge) MCP server provides access to the Red Hat Customer Portal Knowledge Base via the **KCS Hydra API** (`https://access.redhat.com/hydra/rest/search/kcs`). It's relevant because:

**What it does:**
- `searchKnowledgeBase` — search KB for solutions/articles by keyword, with product/type filters
- `getSolution` — get full KB article content (environment, issue, root cause, resolution, diagnostic steps)
- `searchDocumentation` — search product docs (same KCS API, filtered by `documentKind:"Documentation"`)
- `getErrata` — errata/advisory details via CSAF API (public, no auth needed)

**How the KCS search works (Solr-style):**
```
GET https://access.redhat.com/hydra/rest/search/kcs
  ?q={query}
  &rows={max_results}
  &fl=id,title,abstract,documentKind,view_uri,product,lastModifiedDate
  &fq=documentKind:"Documentation"
  &fq=product:"Red Hat Ansible Automation Platform"
```

**Key fields returned:** `id`, `title`, `abstract`, `documentKind`, `view_uri` (points to docs.redhat.com URLs), `product`, `lastModifiedDate`.

**Auth requirement:** Requires a Red Hat offline API token (`REDHAT_TOKEN` from https://access.redhat.com/management/api), exchanged via Red Hat SSO (`sso.redhat.com`) for a short-lived bearer token. Tokens are cached and auto-refreshed.

**Relevance to our approach:**
- The KCS `searchDocumentation` tool could serve as a **live search fallback** (similar to how we use RTD Search API as fallback for upstream docs), but it requires auth — we can't use it as a no-auth default.
- The `view_uri` field confirms that KCS indexes the same docs.redhat.com pages we'd include in our manifests — validates our URL list.
- **Future option:** if users have `REDHAT_TOKEN`, we could offer an optional live KCS search as an enrichment source alongside our static manifests. This would cover KB articles/solutions in addition to product docs.
- The `getErrata` tool uses the public CSAF API (`https://access.redhat.com/hydra/rest/securitydata/csaf/{id}.json`) — no auth needed. Could be useful for security-focused queries.

### URL patterns differ by version

| Version | Pattern | Example |
|---------|---------|---------|
| 2.5 | `/2.5/html/{guide_slug}` | `.../2.5/html/planning_your_installation` |
| 2.6 | `/2.6/{topic_slug}` | `.../2.6/install-proc_installing_containerized_aap` |
| 2.7 | `/2.7/{topic_slug}` | `.../2.7/install-proc_installing_containerized_aap` |

Base URL: `https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform`

### Content negotiation: HTML only (no text/markdown)

docs.redhat.com returns `text/html;charset=utf-8` for all requests, including when `Accept: text/markdown` is sent. This is different from docs.ansible.com (which uses Cloudflare's markdown content negotiation via RTD). **`fetch_doc` will need an HTML-to-markdown conversion path for docs.redhat.com pages.**

### Page counts

- **AAP 2.5:** 38 guide pages
- **AAP 2.6:** 52 topic pages
- **AAP 2.7:** 50 topic pages

### PDF reference copies

PDF copies of all versions are available locally at `~/Claude/aap-docs/` for manifest verification:
- `aap-2.5-docs/` — 8 PDFs (4.1 MB)
- `aap-2.6-docs/` — 16 PDFs (102 MB)
- `aap-2.7-docs/` — 16 PDFs (97 MB)

## Architecture: fits the existing provider-based design

The doc system already has a multi-source manifest architecture (`docs.py` + `config.py`). Adding AAP requires:

### 1. Build AAP manifests (new builder or extension to `manifest_builder.py`)

Build JSON manifests in the same v2.0 format used by existing sources:

```json
{
  "version": "2.0",
  "generated": "2026-07-15T...",
  "base_url": "https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7",
  "files": [
    {
      "path": "install-proc_installing_containerized_aap",
      "topic": "install",
      "title": "Installing containerized AAP",
      "audience": "admin",
      "core": true,
      "summary": "...",
      "lines": 0,
      "tokens": 0,
      "aap_version": "2.7"
    }
  ]
}
```

**Topic derivation:** The 2.6/2.7 topic slugs have a natural prefix (`install-`, `upgrade-`, `configure-`, `secure-`, `observe-`, etc.) that maps directly to topic tags. For 2.5, derive topics from the guide slug names.

**Version tagging:** Each entry gets an `aap_version` field so search results can be filtered or displayed with version context.

**Building the manifests:**
1. Scrape each version's landing page to get the list of guide/topic URLs
2. Fetch each page, extract `<title>` and first meaningful paragraph as summary
3. Use the PDFs in `~/Claude/aap-docs/` to cross-reference/verify titles and coverage
4. Output one manifest JSON per version

### 2. Register AAP sources in `config.py`

Add to `DEFAULT_DOC_SOURCES`:

```python
"aap-2.5": {
    "file": str(_PKG_DIR / "data" / "aap_25_manifest.json"),
    "description": "Red Hat AAP 2.5 — installation, configuration, operations, troubleshooting",
},
"aap-2.6": {
    "file": str(_PKG_DIR / "data" / "aap_26_manifest.json"),
    "description": "Red Hat AAP 2.6 — installation, mesh, EE, RBAC, AI features, MCP server",
},
"aap-2.7": {
    "file": str(_PKG_DIR / "data" / "aap_27_manifest.json"),
    "description": "Red Hat AAP 2.7 — installation, mesh, self-service, metrics, AI features",
},
```

### 3. Extend `fetch_doc` to support docs.redhat.com

**`validate_doc_url` (`validation.py:189`)** currently hardcodes `docs.ansible.com`:
```python
if parsed.scheme != "https" or parsed.netloc != "docs.ansible.com":
    raise ValidationError("URL must start with https://docs.ansible.com/")
```

Change to allow both domains:
```python
ALLOWED_DOC_HOSTS = {"docs.ansible.com", "docs.redhat.com"}

if parsed.scheme != "https" or parsed.netloc not in ALLOWED_DOC_HOSTS:
    raise ValidationError(
        "URL must start with https://docs.ansible.com/ or https://docs.redhat.com/"
    )
```

**`fetch_doc_content` (`docs.py:447`)** checks redirect domain:
```python
if resp.url.host != "docs.ansible.com":
    raise AnsibleKnowError(f"Redirect to unexpected domain: {resp.url.host}")
```

Change to allow both hosts.

**Content handling — REVISED:** With the Red Hat Documentation MCP server (`docs-mcp.api.redhat.com`), we can fetch docs.redhat.com pages as markdown via `redhat_docs_fetch` — no HTML-to-markdown conversion needed. The `fetch_doc_content` branch for docs.redhat.com would:
1. Call `redhat_docs_fetch(url)` via JSON-RPC over HTTP
2. Unwrap the `{"result": "<markdown>"}` envelope
3. Apply similar cleaning as `clean_rtd_markdown`
4. Return the same `FetchDocResult` structure

This eliminates the need for `markdownify` or a custom HTML parser. The only new dependency is the MCP client code (which is ~40 lines of stdlib Python, as demonstrated by `content-crawl-rh-docs-mcp`).

### 4. Version-tagged search results

The `search_docs` tool already returns `source` in results. With AAP manifests registered as `aap-2.5`, `aap-2.6`, `aap-2.7`, the source field naturally identifies the version. Additionally:

- Add `aap_version` to manifest entries for explicit version tagging
- The existing `source` filter parameter in `search_docs` already supports filtering by source name, so `source="aap-2.7"` works out of the box
- Consider adding a `version` filter parameter for convenience

### 5. Update tool descriptions and prompts

- Update `fetch_doc` docstring to mention docs.redhat.com support
- Update `search_docs` description to mention AAP documentation
- Update the `find_collection` and `review_playbook` prompts to mention AAP docs availability

## Implementation Plan

### Phase 1: Red Hat Docs MCP client (`redhat_docs.py`)

Add a `RedHatDocsClient` class to ansible-know (adapted from `McpDocsClient` in `content-crawl-rh-docs-mcp`). Pure stdlib, ~50-80 lines:
- MCP session management (`initialize` + `notifications/initialized`)
- `fetch(url)` → calls `redhat_docs_fetch`, returns markdown or structured JSON
- `search(query)` → calls `redhat_docs_search`, returns results (when it works)
- Session reuse and error handling

Files: new `src/ansible_know/redhat_docs.py`

### Phase 2: Build AAP manifests

Build-time script (extend `manifest_builder.py` or new `aap_manifest_builder.py`):
1. Call `redhat_docs_fetch` on each AAP version landing page
2. Parse the `categoryTitles` JSON → extract category, title, description, URL for each guide
3. Derive topic tags from category names (What's new, Install, Upgrade, Configure, etc.)
4. Tag each entry with `aap_version`
5. Output v2.0 manifest JSON files

Ship: `aap_25_manifest.json`, `aap_26_manifest.json`, `aap_27_manifest.json` in `src/ansible_know/data/`

### Phase 3: Register AAP sources in `config.py`

Add three entries to `DEFAULT_DOC_SOURCES`. Search immediately works via existing `search_docs` tool with `source="aap-2.6"` filtering.

### Phase 4: Extend `fetch_doc` for docs.redhat.com

1. **`validate_doc_url`** — add `docs.redhat.com` to allowed hosts
2. **`fetch_doc_content`** — add branch for docs.redhat.com URLs:
   - Use `RedHatDocsClient.fetch(url)` instead of direct httpx
   - Returns markdown natively — no HTML-to-markdown conversion needed
   - Apply similar cleaning as `clean_rtd_markdown`
3. **Redirect check** — allow `docs.redhat.com` in the domain validation

### Phase 5: Update descriptions and prompts

- `fetch_doc` docstring → mention docs.redhat.com support
- `search_docs` description → mention AAP documentation
- Tool annotation on `fetch_doc` URL parameter

### Phase 6: Tests

- Unit tests: manifest loading, URL validation, RedHatDocsClient (mocked)
- Integration tests: live MCP fetch against AAP landing + guide pages
- Cross-reference manifest entries against PDF table of contents

## API Test Results (2026-07-15)

### `redhat_docs_fetch` — landing pages

All three AAP version landing pages return structured JSON via the MCP server:

| Version | Guides found | Categories |
|---------|-------------|------------|
| 2.5 | 38 | traditional guide structure |
| 2.6 | 53 | What's new, Technology preview, Get started, Plan, Install, Extend, Upgrade, Migrate, Secure, Administer, Develop, Configure, Integrate, Observe, Optimize, Troubleshoot, Reference, Download PDF |
| 2.7 | 50 | similar to 2.6 |

Landing JSON structure:
```json
{
  "product": "Red Hat Ansible Automation Platform",
  "version": "2.6",
  "categoryTitles": {
    "Install": {
      "description": "",
      "titles": [
        { "name": "Install containerized Ansible Automation Platform", "description": "", "url": "https://docs.redhat.com/.../install-proc_installing_containerized_aap" }
      ]
    }
  }
}
```

### `redhat_docs_fetch` — individual guides

| Version | Result |
|---------|--------|
| 2.5 | All guides work, returns full markdown (52K+ chars per guide via `/html-single/` URLs) |
| 2.6 | 45/53 guides work, 8 fail with 404 (URL issues: trailing spaces, mixed-case like `Extend-`) |
| 2.7 | Not tested individually, likely similar to 2.6 |

Failed 2.6 guides (server-side URL validation issues):
- `whats_new-ansible_core_2_19`, `install-assembly_operator_install_operator`
- `install-assembly_aap_activate_1`, `install-assembly_devtools_install`
- `install-assembly_rhdh_intro`, `install-proc_installing_builder`
- `Extend-assembly_deploying_ansible_mcp_server`, `Extend-assembly_deploying_alia`

### `redhat_docs_search`

**Works, but with significant limitations.** Initial empty results were caused by session expiry — the MCP server sessions expire quickly and return HTTP 404. With fresh sessions per query, search returns relevant results.

**Behavior:**
- Returns max **2 results** per query (as documented: "returns 2 high-quality chunks")
- Each result has `title`, `content` (markdown preview), and `url`
- Searches **all Red Hat docs** — no product filter parameter, returns results across RHEL, OpenShift, AAP, Satellite, etc.
- Input must be in English

**Intermittent 404s:** Same query returns HTTP 404 ~30-40% of the time. Confirmed non-deterministic by running "automation mesh" 5 times: got 404, 2 results, 404, 2 results, 2 results. Cause is server-side (load balancing, cold starts, or session management). **Retry with session re-init resolves it.**

**Version bias:** Results skew heavily toward older AAP versions (2.2, 2.4, 2.5). Never returned 2.6 or 2.7 content in any test. The newer topic-based URL structure for 2.6/2.7 may not be indexed yet.

**Tested queries and results:**

| Query | Results | Versions |
|-------|---------|----------|
| `automation mesh` | Mesh for VM environments, Setting up mesh | 2.5, 2.2 |
| `execution environment` | EE chapters | 2.5, 2.4 |
| `automation portal self-service` | Installing/Configuring portal | 2.5, 2.5 |
| `ansible EDA event-driven` | EDA controller overview/installation | 2.4, 2.4 |
| `ansible lightspeed` | Lightspeed user guide | 2.x_latest |
| `automation controller job template` | Job templates chapters | 2.4, 2.5 |
| `troubleshooting ansible` | Troubleshooting guides | 2.5, 2.4 |
| `MCP server deploy` | Satellite MCP (not AAP!) | 6.18, 6.19 |
| `ansible automation platform install` | Install guides | 2.4, 2.2 |
| `RBAC` | RBAC policies | 2 results |
| `upgrade ansible platform` | 0 results | — |
| `hashicorp vault ansible` | 0 results | — |

**Conclusion:** `redhat_docs_search` is useful as a **supplementary live fallback** (similar to RTD Search API for upstream docs) but cannot be the primary search source:
- Only 2 results max per query
- No product/version filtering
- Intermittent 404s requiring retry logic
- No AAP 2.6/2.7 coverage
- **Static manifests remain the right primary approach** for reliable, version-tagged AAP doc search

### `tools/list` — server capabilities

```
redhat_docs_search: Search official Red Hat Documentation (returns 2 chunks per query)
redhat_docs_fetch:  Fetch and convert a Red Hat Documentation page to markdown
```

Server info: FastMCP-based, protocol version `2024-11-05`, supports streamable HTTP transport.

## Open Questions

- [x] ~~HTML-to-markdown approach~~ → RESOLVED: `redhat_docs_fetch` returns markdown natively
- [x] ~~Embed MCP client or add dependency?~~ → RESOLVED: Adapt ~50 lines of stdlib Python into `redhat_docs.py`
- [ ] Dedicated `aap_version` filter in `search_docs`, or is `source` filter sufficient?
- [ ] Rate limiting for `docs-mcp.api.redhat.com` — unknown server limits, need conservative defaults
- [ ] Should manifests be rebuilt on release, or are AAP docs stable enough to ship static?
- [ ] 8 failed 2.6 guides — report upstream to DocX team? Work around with direct curl fallback?
- [x] ~~`redhat_docs_search` returning empty~~ → RESOLVED: works with fresh sessions, but limited (2 results max, no version filter, intermittent 404s, no 2.6/2.7 coverage). Use as optional live fallback with retry logic, not primary source.

## Available Guide/Topic Slugs

### AAP 2.5 — 38 guides

```
access_management_and_authentication
ansible_automation_platform_migration
automation_execution_api_overview
automation_mesh_for_managed_cloud_or_operator_environments
automation_mesh_for_vm_environments
backup_and_recovery_for_operator_environments
configuring_automation_execution
configuring_self-service_automation_portal
containerized_installation
creating_and_using_execution_environments
developing_automation_content
getting_started_with_ansible_automation_platform
getting_started_with_hashicorp_and_ansible_automation_platform
getting_started_with_playbooks
hardening_and_compliance
implementing_security_automation
installing_ansible_plug-ins_for_red_hat_developer_hub
installing_on_openshift_container_platform
installing_self-service_automation_portal
managing_automation_content
managing_device_fleets_with_the_red_hat_edge_manager
operating_ansible_automation_platform
performance_considerations_for_operator_environments
performance_tuning_for_ansible_automation_platform
planning_your_installation
release_notes
rpm_installation
rpm_upgrade_and_migration
tested_deployment_models
troubleshooting_ansible_automation_platform
using_ansible_development_workspaces_for_automation_content_development
using_ansible_plug-ins_for_red_hat_developer_hub
using_automation_analytics
using_automation_dashboard
using_automation_decisions
using_automation_execution
using_content_navigator
using_self-service_automation_portal
```

### AAP 2.6 — 52 topics

```
administer-assembly_aap_backup
administer-assembly_planning_mesh
configure-assembly_gw_settings
configure-configure_a_proxy_to_communicate_with_external_systems
configure-distribute_workloads_with_clustering
develop-assembly_intro_to_playbooks
download_pdf-ansible_automation_platform_pdfs
Extend-assembly_deploying_alia
Extend-assembly_deploying_ansible_mcp_server
extend-enable_ai_in_the_ansible_vs_code_extension_with_the_mcp_server
get_started-assembly_gs_auto_dev
get_started-assembly_intro_to_playbooks_1
install-assembly_aap_activate_1
install-assembly_appendix_inventory_file_vars
install-assembly_devtools_install
install-assembly_operator_install_operator
install-assembly_platform_install_overview
install-assembly_rhdh_intro
install-assembly_self_service_about
install-assembly_view_key_metrics
install-proc_installing_builder
install-proc_installing_containerized_aap
integrate-assembly_controller_pac
integrate-assembly_terraform_introduction
integrate-assembly_ug_controller_setting_up_insights
integrate-assembly_vault_introduction
migrate-con_introduction_and_objectives
observe-assembly_controller_logging_aggregation
observe-assembly_metrics_utility
observe-ensure_system_health_and_efficiency_through_monitoring
optimize-assembly_controller_improving_performance
optimize-con_user_data_tracking
optimize-optimize_platform_performance
plan-assembly_overview_tested_deployment_models
plan-proc_attaching_subscriptions
reference-ansible_automation_platform_custom_resources
secure-assembly_gw_configure_authentication
troubleshoot-proc_settings_troubleshooting
upgrade-ansible_automation_platform_upgrade
upgrade-assembly_rhdh_upgrade_ocp_helm
upgrade-assembly_self_service_upgrading
upgrade-assembly_upgrade_data_movement
upgrade-assembly_upgrade_support_matrix
upgrade-con_aap_upgrade_overview
upgrade-proc_upgrading_automation_dashboard
upgrade-ref_upgrade_scenarios_container
upgrade-ref_upgrade_scenarios_openshift
upgrade-upgrade_additional_services_for_ansible_automation_platform
whats_new-aap_26
whats_new-ansible_core_2_19
whats_new-assembly_workspaces_intro
whats_new-async_updates
```

### AAP 2.7 — 50 topics

```
administer-assembly_planning_mesh
administer-back_up_and_restore_your_containerized_deployment
configure-assembly_gw_settings
configure-configure_a_proxy_to_communicate_with_external_systems
configure-distribute_workloads_with_clustering
develop-assembly_intro_to_playbooks
discover-what_is_ansible_automation_platform
download_pdf-ansible_automation_platform_pdf_reference
extend-assembly_deploying_ansible_mcp_server
extend-assembly_rhdh_intro
get_started-assembly_gs_auto_dev
get_started-assembly_gs_platform_admin
install-assembly_aap_activate
install-assembly_appendix_inventory_file_vars
install-assembly_devtools_install
install-assembly_operator_install_operator
install-assembly_self_service_about
install-assembly_view_key_metrics
install-con_self_service_rhel_appliances
install-con_understand_metrics_service_architecture
install-proc_installing_builder
install-proc_installing_containerized_aap
integrate-assembly_controller_pac
integrate-assembly_terraform_introduction
integrate-assembly_ug_controller_setting_up_insights
integrate-assembly_vault_introduction
migrate-migrate_from_existing_deployment_topologies
observe-assembly_controller_logging_aggregation
observe-assembly_metrics_utility
observe-ensure_system_health_and_efficiency_through_monitoring
optimize-assembly_controller_improving_performance
optimize-con_user_data_tracking
optimize-optimize_platform_performance
plan-assembly_overview_tested_deployment_models
reference-ansible_automation_platform_custom_resources
secure-assembly_gw_configure_authentication
secure-assembly_gw_managing_access
troubleshoot-proc_settings_troubleshooting
troubleshoot-ref_troubleshoot_metrics_service
upgrade-assembly_operator_upgrade
upgrade-assembly_rhdh_upgrade_ocp_helm
upgrade-assembly_self_service_upgrading
upgrade-plan_your_ansible_automation_platform_upgrade
upgrade-proc_self_service_rhel_upgrade
upgrade-proc_update_aap_container
upgrade-upgrade_additional_services_for_ansible_automation_platform
whats_new-bring_your_own_knowledge_with_the_automation_intelligent_assistant
whats_new-con_understand_automation_dashboard_architecture
whats_new-oidc_authentication_for_hashicorp_vault
whats_new-overview_of_redhat_ansible_intro
```
