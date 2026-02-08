(function () {
    const sectorSelect = document.getElementById("sector_select");
    const searchInput = document.getElementById("search_skills");
    const skillsContainer = document.getElementById("skills_container");
    const skillCount = document.getElementById("skill_count");
    const fetchForm = document.getElementById("fetch_form");
    const runState = document.getElementById("run_state");
    const runSummary = document.getElementById("run_summary");
    const submitBtn = document.getElementById("submit_btn");
    const refreshMongoBtn = document.getElementById("refresh_mongo_btn");
    const mongoState = document.getElementById("mongo_state");
    const mongoMeta = document.getElementById("mongo_meta");
    const mongoRecentRows = document.getElementById("mongo_recent_rows");
    const runsRows = document.getElementById("runs_rows");
    const selectAllBtn = document.getElementById("select_all_btn");
    const deselectAllBtn = document.getElementById("deselect_all_btn");
    const quotaBanner = document.getElementById("quota_banner");
    const searchMaxResultsInput = document.getElementById("search_max_results");
    const commentsMaxResultsInput = document.getElementById("comments_max_results");
    const publishedAfterInput = document.getElementById("published_after");
    const publishedBeforeInput = document.getElementById("published_before");
    const regionCodeInput = document.getElementById("region_code");
    const relevanceLanguageInput = document.getElementById("relevance_language");
    const videoDurationInput = document.getElementById("video_duration");
    const minViewCountInput = document.getElementById("min_view_count");
    const minLikeCountInput = document.getElementById("min_like_count");
    let selectedSkills = new Set();

    function esc(text) {
        const value = String(text ?? "");
        return value
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function setRunState(message, statusType = "idle", showSpinner = false) {
        runState.className = "status";
        if (statusType === "running") runState.classList.add("running");
        if (statusType === "success") runState.classList.add("success");
        if (statusType === "error") runState.classList.add("error");
        if (statusType === "warning") runState.classList.add("warning");
        runState.innerHTML = showSpinner
            ? `<span class="spinner"></span>${esc(message)}`
            : esc(message);
    }

    function setQuotaBanner(quota) {
        quotaBanner.className = "status";
        if (!quota) {
            quotaBanner.textContent = "Quota estimate unavailable.";
            return;
        }

        const msg = `Estimated units: ${quota.estimated_units} / ${quota.daily_limit} (remaining after run: ${quota.remaining_after_run})`;
        if (quota.level === "over_limit") {
            quotaBanner.classList.add("error");
            quotaBanner.textContent = `${msg}. This likely exceeds daily quota.`;
        } else if (quota.level === "warning") {
            quotaBanner.classList.add("warning");
            quotaBanner.textContent = `${msg}. Warning: this is near your daily quota.`;
        } else {
            quotaBanner.classList.add("success");
            quotaBanner.textContent = msg;
        }
    }

    function updateSelectedCount() {
        skillCount.textContent = `Selected: ${selectedSkills.size}`;
    }

    function renderSkills(skills) {
        skillsContainer.innerHTML = "";
        if (!skills.length) {
            skillsContainer.textContent = "No skills found for this filter.";
            return;
        }
        skills.forEach((skill) => {
            const row = document.createElement("label");
            row.className = "skill-item";

            const checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.value = skill;
            checkbox.checked = selectedSkills.has(skill);
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    selectedSkills.add(skill);
                } else {
                    selectedSkills.delete(skill);
                }
                updateSelectedCount();
                updateQuotaEstimate();
            });

            row.appendChild(checkbox);
            row.append(" " + skill);
            skillsContainer.appendChild(row);
        });
    }

    async function loadSkills(sector, searchTerm = "") {
        skillsContainer.textContent = "Loading skills...";
        const response = await fetch("/search_skills", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sector: sector, search_term: searchTerm }),
        });
        if (!response.ok) {
            throw new Error("Could not load skills.");
        }
        const skills = await response.json();
        renderSkills(skills);
    }

    function collectPayload() {
        return {
            sector: sectorSelect.value,
            api_key: document.getElementById("api_key").value,
            search_max_results: searchMaxResultsInput.value,
            search_order: document.getElementById("search_order").value,
            comments_max_results: commentsMaxResultsInput.value,
            skills: Array.from(selectedSkills),
            published_after: publishedAfterInput.value,
            published_before: publishedBeforeInput.value,
            region_code: regionCodeInput.value,
            relevance_language: relevanceLanguageInput.value,
            video_duration: videoDurationInput.value,
            min_view_count: minViewCountInput.value,
            min_like_count: minLikeCountInput.value,
        };
    }

    const summaryDefinitions = {
        "Run ID": "Unique identifier for this ingestion run.",
        "Requested Skills": "Unique skills selected for the run (duplicates removed).",
        "Processed Skills":
            "Skills actually processed before stopping early (quota/errors can stop a run).",
        "Videos Found": "Total search results returned across processed skills.",
        "Videos Processed":
            "Search results with a videoId that the run attempted to inspect.",
        "Filtered By Constraints":
            "Videos skipped because view/like counts were below the minimums.",
        "Upserts Attempted":
            "Mongo upsert operations executed after filtering and API calls.",
        Inserted: "New documents created by upserts.",
        Updated: "Existing documents updated by upserts.",
        Unchanged: "Existing documents already matching; no change applied.",
        "Error Count": "Total errors captured during search, video, or comments calls.",
        "Quota Exceeded": "Whether a quota error was detected; run stops early when yes.",
    };

    function renderSummary(summary, runId) {
        const errors = summary.errors || [];
        const rows = [
            { key: "Run ID", value: runId || "-" },
            { key: "Requested Skills", value: summary.skills_requested },
            { key: "Processed Skills", value: summary.skills_processed },
            { key: "Videos Found", value: summary.videos_found },
            { key: "Videos Processed", value: summary.videos_processed },
            {
                key: "Filtered By Constraints",
                value: summary.videos_filtered_constraints,
            },
            { key: "Upserts Attempted", value: summary.upserts_attempted },
            { key: "Inserted", value: summary.inserted },
            { key: "Updated", value: summary.updated },
            { key: "Unchanged", value: summary.unchanged },
            { key: "Error Count", value: summary.error_count },
            {
                key: "Quota Exceeded",
                value: summary.quota_exceeded ? "yes" : "no",
            },
        ];

        const kvHtml = rows
            .map(({ key, value }) => {
                const help = summaryDefinitions[key] || "";
                return `
                    <div class="summary-kv">
                        <strong>${esc(key)}</strong>
                        <div>${esc(value)}</div>
                        ${help ? `<div class="summary-help">${esc(help)}</div>` : ""}
                    </div>
                `;
            })
            .join("");

        let errorsHtml = "";
        if (errors.length) {
            errorsHtml = `
                <div class="error-list mono">
                    ${errors.map((e) => `<div>${esc(e)}</div>`).join("")}
                </div>
            `;
        }

        runSummary.innerHTML = `
            <h3>Last Run Summary</h3>
            <div class="summary-grid">${kvHtml}</div>
            ${errorsHtml}
        `;
    }

    function renderMongoStatus(payload) {
        const status = payload.status || {};
        const rows = status.recent_docs || [];

        mongoMeta.innerHTML = `
            <div class="mongo-item"><strong>DB</strong><div class="mono">${esc(status.database || "-")}</div></div>
            <div class="mongo-item"><strong>Collection</strong><div class="mono">${esc(status.collection || "-")}</div></div>
            <div class="mongo-item"><strong>Total Docs</strong><div>${esc(status.total_documents ?? 0)}</div></div>
            <div class="mongo-item"><strong>Sectors</strong><div>${esc(status.total_sectors ?? 0)}</div></div>
            <div class="mongo-item"><strong>Skills</strong><div>${esc(status.total_skills ?? 0)}</div></div>
            <div class="mongo-item"><strong>Ingested Last 24h</strong><div>${esc(status.ingested_last_24h ?? 0)}</div></div>
            <div class="mongo-item"><strong>Duplicate Groups</strong><div>${esc(status.duplicate_groups ?? 0)}</div></div>
        `;

        if (!rows.length) {
            mongoRecentRows.innerHTML = `<tr><td colspan="4">No documents found.</td></tr>`;
        } else {
            mongoRecentRows.innerHTML = rows
                .map(
                    (row) => `
                <tr>
                    <td>${esc(row.ingested_timing || "-")}</td>
                    <td>${esc(row.skill_name || "-")}</td>
                    <td class="mono">${esc(row.videoId || "-")}</td>
                    <td>${esc(row.title || "-")}</td>
                </tr>
            `
                )
                .join("");
        }
    }

    function renderRuns(runs) {
        if (!runs || !runs.length) {
            runsRows.innerHTML = `<tr><td colspan="4">No run history yet.</td></tr>`;
            return;
        }
        runsRows.innerHTML = runs
            .map((run) => {
                const started = run.started_at || "-";
                const status = run.status || "-";
                const skills = run.params?.skills_count ?? "-";
                const upserts = run.summary?.upserts_attempted ?? "-";
                return `
                    <tr>
                        <td>${esc(started)}</td>
                        <td>${esc(status)}</td>
                        <td>${esc(skills)}</td>
                        <td>${esc(upserts)}</td>
                    </tr>
                `;
            })
            .join("");
    }

    async function refreshMongoStatus() {
        mongoState.className = "status";
        mongoState.textContent = "Refreshing MongoDB status...";
        try {
            const response = await fetch("/mongo_status");
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Unable to load MongoDB status");
            }
            renderMongoStatus(payload);
            renderRuns(payload.recent_runs || []);
            mongoState.className = "status success";
            mongoState.textContent = "MongoDB status is up to date.";
        } catch (error) {
            mongoState.className = "status error";
            mongoState.textContent = `MongoDB status error: ${error.message}`;
        }
    }

    async function updateQuotaEstimate() {
        try {
            const response = await fetch("/quota_estimate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(collectPayload()),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || "Unable to estimate quota");
            }
            setQuotaBanner(result.quota);
        } catch (error) {
            quotaBanner.className = "status error";
            quotaBanner.textContent = `Quota estimate error: ${error.message}`;
        }
    }

    fetchForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!selectedSkills.size) {
            setRunState("Select at least one skill before running.", "error");
            return;
        }

        const payload = collectPayload();
        submitBtn.disabled = true;
        refreshMongoBtn.disabled = true;
        runSummary.innerHTML = "";
        setRunState(
            "Fetching from YouTube and upserting into MongoDB...",
            "running",
            true
        );

        try {
            const response = await fetch("/fetch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || "Ingestion failed");
            }

            let statusType = "success";
            if (result.summary?.quota_exceeded) {
                statusType = "warning";
            }
            setRunState(result.message || "Ingestion completed.", statusType);
            renderSummary(result.summary || {}, result.run_id);
            setQuotaBanner(result.quota || null);
            await refreshMongoStatus();
        } catch (error) {
            setRunState(`Ingestion failed: ${error.message}`, "error");
        } finally {
            submitBtn.disabled = false;
            refreshMongoBtn.disabled = false;
        }
    });

    selectAllBtn.addEventListener("click", () => {
        const checkboxes = skillsContainer.querySelectorAll("input[type='checkbox']");
        checkboxes.forEach((cb) => {
            cb.checked = true;
            selectedSkills.add(cb.value);
        });
        updateSelectedCount();
        updateQuotaEstimate();
    });

    deselectAllBtn.addEventListener("click", () => {
        const checkboxes = skillsContainer.querySelectorAll("input[type='checkbox']");
        checkboxes.forEach((cb) => {
            cb.checked = false;
            selectedSkills.delete(cb.value);
        });
        updateSelectedCount();
        updateQuotaEstimate();
    });

    sectorSelect.addEventListener("change", async () => {
        selectedSkills = new Set();
        updateSelectedCount();
        try {
            await loadSkills(sectorSelect.value, searchInput.value);
            await updateQuotaEstimate();
        } catch (error) {
            setRunState(`Skill loading failed: ${error.message}`, "error");
        }
    });

    searchInput.addEventListener("input", async () => {
        try {
            await loadSkills(sectorSelect.value, searchInput.value);
        } catch (error) {
            setRunState(`Skill loading failed: ${error.message}`, "error");
        }
    });

    [
        searchMaxResultsInput,
        commentsMaxResultsInput,
        minViewCountInput,
        minLikeCountInput,
        publishedAfterInput,
        publishedBeforeInput,
        regionCodeInput,
        relevanceLanguageInput,
        videoDurationInput,
    ].forEach((el) => el.addEventListener("input", updateQuotaEstimate));

    refreshMongoBtn.addEventListener("click", refreshMongoStatus);

    async function bootstrap() {
        updateSelectedCount();
        if (sectorSelect.value) {
            try {
                await loadSkills(sectorSelect.value, "");
            } catch (error) {
                setRunState(`Initial skill loading failed: ${error.message}`, "error");
            }
        } else {
            skillsContainer.textContent = "No sectors found. Seed MySQL first.";
        }
        await updateQuotaEstimate();
        await refreshMongoStatus();
    }

    bootstrap();
})();
