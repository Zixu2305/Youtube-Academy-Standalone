(function () {
    const sectorSelect = document.getElementById("sector_select");
    const searchInput = document.getElementById("search_skills");
    const skillsContainer = document.getElementById("skills_container");
    const competenciesContainer = document.getElementById("competencies_container");
    const proficiencyContainer = document.getElementById("proficiency_container");
    const requirementsContainer = document.getElementById("requirements_container");
    const fetchForm = document.getElementById("fetch_form");
    const runState = document.getElementById("run_state");
    const runSummary = document.getElementById("run_summary");
    const submitBtn = document.getElementById("submit_btn");
    const refreshMongoBtn = document.getElementById("refresh_mongo_btn");
    const mongoState = document.getElementById("mongo_state");
    const mongoMeta = document.getElementById("mongo_meta");
    const mongoRecentRows = document.getElementById("mongo_recent_rows");
    const runsRows = document.getElementById("runs_rows");
    const quotaBanner = document.getElementById("quota_banner");
    const searchMaxResultsInput = document.getElementById("search_max_results");
    const publishedAfterInput = document.getElementById("published_after");
    const publishedBeforeInput = document.getElementById("published_before");
    const regionCodeInput = document.getElementById("region_code");
    const relevanceLanguageInput = document.getElementById("relevance_language");
    const additionalQueryInput = document.getElementById("additional_query");
    const includeSectorQueryInput = document.getElementById("include_sector_query");
    const includeSkillQueryInput = document.getElementById("include_skill_query");
    const includeCompetencyQueryInput = document.getElementById("include_competency_query");
    const includeRequirementQueryInput = document.getElementById("include_requirement_query");
    const maxVideoAgeInput = document.getElementById("max_video_age");
    const minViewCountInput = document.getElementById("min_view_count");
    const minLikeCountInput = document.getElementById("min_like_count");
    const minVideoLengthInput = document.getElementById("min_video_length");
    const maxVideoLengthInput = document.getElementById("max_video_length");
    const minCommentCountInput = document.getElementById("min_comment_count");
    const previewSection = document.getElementById("preview_section");
    const previewVideos = document.getElementById("preview_videos");
    const upsertSelectedBtn = document.getElementById("upsert_selected_btn");
    const cancelPreviewBtn = document.getElementById("cancel_preview_btn");
    let selectedSkill = null;
    let selectedCompetency = null;
    let selectedProficiency = null;
    let selectedRequirement = null;
    let previewData = null;

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

    function renderSkills(skills) {
        skillsContainer.innerHTML = "";
        if (!skills.length) {
            skillsContainer.textContent = "No skills found for this filter.";
            return;
        }
        skills.forEach((skill) => {
            const row = document.createElement("div");
            row.className = "skill-item";

            const skillButton = document.createElement("span");
            skillButton.className = selectedSkill === skill ? "skill-name selected" : "skill-name";
            skillButton.textContent = skill;
            skillButton.addEventListener("click", () => {
                if (selectedSkill === skill) {
                    selectedSkill = null;
                    skillButton.className = "skill-name";
                } else {
                    // Clear previous selection
                    const prevSelected = document.querySelector(".skill-name.selected");
                    if (prevSelected) {
                        prevSelected.className = "skill-name";
                    }
                    selectedSkill = skill;
                    skillButton.className = "skill-name selected";
                }
                // Clear downstream selections
                selectedCompetency = null;
                selectedProficiency = null;
                selectedRequirement = null;
                loadCompetencies();
                updateQuotaEstimate();
            });

            row.appendChild(skillButton);
            skillsContainer.appendChild(row);
        });
    }

    async function loadCompetencies() {
        competenciesContainer.innerHTML = "";
        proficiencyContainer.innerHTML = "";
        requirementsContainer.innerHTML = "";
        selectedCompetency = null;
        selectedProficiency = null;
        selectedRequirement = null;
        if (!selectedSkill) {
            competenciesContainer.textContent = "Select a skill first";
            return;
        }

        try {
            const response = await fetch("/search_competencies", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector: sectorSelect.value, skill: selectedSkill })
            });
            const competencies = await response.json();
            if (!competencies.length) {
                competenciesContainer.textContent = "No competencies found.";
                return;
            }
            competencies.forEach((comp) => {
                const row = document.createElement("div");
                row.className = "competency-item";

                const compButton = document.createElement("span");
                compButton.className = selectedCompetency === comp ? "competency-name selected" : "competency-name";
                compButton.textContent = comp;
                compButton.addEventListener("click", () => {
                    if (selectedCompetency === comp) {
                        selectedCompetency = null;
                        compButton.className = "competency-name";
                    } else {
                        // Clear previous selection
                        const prevSelected = document.querySelector(".competency-name.selected");
                        if (prevSelected) {
                            prevSelected.className = "competency-name";
                        }
                        selectedCompetency = comp;
                        compButton.className = "competency-name selected";
                    }
                    // Clear downstream
                    selectedProficiency = null;
                    selectedRequirement = null;
                    loadProficiencyLevels();
                    updateQuotaEstimate();
                });

                row.appendChild(compButton);
                competenciesContainer.appendChild(row);
            });
        } catch (error) {
            competenciesContainer.textContent = "Error loading competencies";
        }
    }

    async function loadProficiencyLevels() {
        proficiencyContainer.innerHTML = "";
        requirementsContainer.innerHTML = "";
        selectedProficiency = null;
        selectedRequirement = null;
        if (!selectedSkill || !selectedCompetency) {
            proficiencyContainer.textContent = "Select a competency first";
            return;
        }

        try {
            const response = await fetch("/search_proficiency_levels", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector: sectorSelect.value, skill: selectedSkill, competency: selectedCompetency })
            });
            const levels = await response.json();
            if (!levels.length) {
                proficiencyContainer.textContent = "No proficiency levels found.";
                return;
            }
            levels.forEach((level) => {
                const row = document.createElement("div");
                row.className = "proficiency-item";

                const levelButton = document.createElement("span");
                levelButton.className = selectedProficiency === level ? "proficiency-name selected" : "proficiency-name";
                levelButton.textContent = level;
                levelButton.addEventListener("click", () => {
                    if (selectedProficiency === level) {
                        selectedProficiency = null;
                        levelButton.className = "proficiency-name";
                    } else {
                        // Clear previous selection
                        const prevSelected = document.querySelector(".proficiency-name.selected");
                        if (prevSelected) {
                            prevSelected.className = "proficiency-name";
                        }
                        selectedProficiency = level;
                        levelButton.className = "proficiency-name selected";
                    }
                    // Clear downstream
                    selectedRequirement = null;
                    loadRequirements();
                    updateQuotaEstimate();
                });

                row.appendChild(levelButton);
                proficiencyContainer.appendChild(row);
            });
        } catch (error) {
            proficiencyContainer.textContent = "Error loading proficiency levels";
        }
    }

    async function loadRequirements() {
        requirementsContainer.innerHTML = "";
        selectedRequirement = null;
        if (!selectedSkill || !selectedCompetency || !selectedProficiency) {
            requirementsContainer.textContent = "Select proficiency level first";
            return;
        }

        try {
            const response = await fetch("/get_requirement", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector: sectorSelect.value, skill: selectedSkill, competency: selectedCompetency, proficiency: selectedProficiency })
            });
            const data = await response.json();
            const requirements = data.requirements || [];
            if (!requirements.length) {
                requirementsContainer.textContent = "No requirements found.";
                return;
            }
            requirements.forEach((req) => {
                const row = document.createElement("div");
                row.className = "requirement-item";

                const reqButton = document.createElement("span");
                reqButton.className = selectedRequirement === req ? "requirement-name selected" : "requirement-name";
                reqButton.textContent = req;
                reqButton.addEventListener("click", () => {
                    if (selectedRequirement === req) {
                        selectedRequirement = null;
                        reqButton.className = "requirement-name";
                    } else {
                        // Clear previous selection
                        const prevSelected = document.querySelector(".requirement-name.selected");
                        if (prevSelected) {
                            prevSelected.className = "requirement-name";
                        }
                        selectedRequirement = req;
                        reqButton.className = "requirement-name selected";
                    }
                    updateQuotaEstimate();
                });

                row.appendChild(reqButton);
                requirementsContainer.appendChild(row);
            });
            if (requirements.length === 1) {
                // Auto-select if only one
                const firstButton = requirementsContainer.querySelector(".requirement-name");
                firstButton.className = "requirement-name selected";
                selectedRequirement = requirements[0];
            }
        } catch (error) {
            requirementsContainer.textContent = "Error loading requirements";
        }
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
            skills: selectedSkill ? [selectedSkill] : [],
            competency: selectedCompetency || "",
            proficiency: selectedProficiency || "",
            requirement: selectedRequirement || "",
            include_sector: includeSectorQueryInput.checked,
            include_skill: includeSkillQueryInput.checked,
            include_competency: includeCompetencyQueryInput.checked,
            include_requirement: includeRequirementQueryInput.checked,
            published_after: publishedAfterInput.value,
            published_before: publishedBeforeInput.value,
            max_video_age: maxVideoAgeInput.value,
            region_code: regionCodeInput.value,
            relevance_language: relevanceLanguageInput.value,
            additional_query: additionalQueryInput.value,
            min_view_count: minViewCountInput.value,
            min_like_count: minLikeCountInput.value,
            min_video_length: document.getElementById("min_video_length").value,
            max_video_length: document.getElementById("max_video_length").value,
            min_comment_count: document.getElementById("min_comment_count").value,
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
        "Search Query": "The query string sent to YouTube's search API.",
    };

    function renderSummary(summary, runId) {
        const errors = summary.errors || [];
        const rows = [
            { key: "Run ID", value: runId || "-" },
        ];

        // Add different fields based on summary type
        if (summary.skills_requested !== undefined) {
            rows.push(
                { key: "Requested Skills", value: summary.skills_requested },
                { key: "Processed Skills", value: summary.skills_processed },
                { key: "Videos Found", value: summary.videos_found },
                { key: "Videos Filtered By Constraints", value: summary.videos_filtered_constraints }
            );
        }

        if (summary.upserts_attempted !== undefined) {
            rows.push(
                { key: "Upserts Attempted", value: summary.upserts_attempted },
                { key: "Inserted", value: summary.inserted },
                { key: "Updated", value: summary.updated },
                { key: "Unchanged", value: summary.unchanged }
            );
        }

        if (summary.videos_to_upsert !== undefined) {
            rows.push(
                { key: "Videos to Upsert", value: summary.videos_to_upsert },
                { key: "Inserted", value: summary.inserted },
                { key: "Updated", value: summary.updated },
                { key: "Unchanged", value: summary.unchanged }
            );
        }

        rows.push(
            { key: "Error Count", value: summary.error_count },
            { key: "Quota Exceeded", value: summary.quota_exceeded ? "yes" : "no" }
        );

        // Add query if available
        if (summary.constraints && summary.constraints.query) {
            rows.push({ key: "Search Query", value: summary.constraints.query });
        }

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
        if (!selectedSkill) {
            setRunState("Select a skill before running.", "error");
            return;
        }

        const hasCheckedQueryField =
            includeSectorQueryInput.checked ||
            includeSkillQueryInput.checked ||
            includeCompetencyQueryInput.checked ||
            includeRequirementQueryInput.checked;
        const hasAdditionalQuery = additionalQueryInput.value.trim() !== "";
        if (!hasCheckedQueryField && !hasAdditionalQuery) {
            setRunState(
                "Select at least one query field (Sector, Skill, Competencies, Requirement) or fill Additional Query.",
                "error"
            );
            return;
        }

        const payload = collectPayload();
        submitBtn.disabled = true;
        refreshMongoBtn.disabled = true;
        runSummary.innerHTML = "";
        setRunState(
            "Fetching videos from YouTube...",
            "running",
            true
        );

        try {
            const response = await fetch("/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || "Preview failed");
            }

            previewData = result;
            setRunState("Videos fetched successfully. Review and select videos to upsert.", "success");
            renderSummary(result.summary || {}, null);
            setQuotaBanner(result.quota || null);
            showPreview(result.videos || []);
        } catch (error) {
            setRunState(`Preview failed: ${error.message}`, "error");
        } finally {
            submitBtn.disabled = false;
            refreshMongoBtn.disabled = false;
        }
    });

    sectorSelect.addEventListener("change", async () => {
        selectedSkill = null;
        selectedCompetency = null;
        selectedProficiency = null;
        selectedRequirement = null;
        loadCompetencies();
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
        minViewCountInput,
        minLikeCountInput,
        minVideoLengthInput,
        maxVideoLengthInput,
        minCommentCountInput,
        publishedAfterInput,
        publishedBeforeInput,
        maxVideoAgeInput,
        regionCodeInput,
        relevanceLanguageInput,
        additionalQueryInput,
        includeSectorQueryInput,
        includeSkillQueryInput,
        includeCompetencyQueryInput,
        includeRequirementQueryInput,
    ].forEach((el) => el.addEventListener("input", updateQuotaEstimate));

    [
        includeSectorQueryInput,
        includeSkillQueryInput,
        includeCompetencyQueryInput,
        includeRequirementQueryInput,
    ].forEach((el) => el.addEventListener("change", updateQuotaEstimate));

    refreshMongoBtn.addEventListener("click", refreshMongoStatus);

    function showPreview(videos) {
        previewVideos.innerHTML = "";
        
        if (videos.length === 0) {
            previewVideos.innerHTML = "<p>No videos found matching the criteria.</p>";
            return;
        }

        const table = document.createElement("table");
        table.innerHTML = `
            <thead>
                <tr>
                    <th><input type="checkbox" id="select_all"></th>
                    <th>Thumbnail</th>
                    <th>Title</th>
                    <th>Channel</th>
                    <th>Views</th>
                    <th>Likes</th>
                    <th>Duration</th>
                    <th>Skill</th>
                </tr>
            </thead>
            <tbody></tbody>
        `;
        
        const tbody = table.querySelector("tbody");
        const selectAllCheckbox = table.querySelector("#select_all");
        
        videos.forEach((video, index) => {
            const row = document.createElement("tr");
            row.innerHTML = `
                <td><input type="checkbox" class="video_checkbox" data-index="${index}"></td>
                <td><img src="${esc(video.thumbnailUrl)}" alt="Thumbnail" style="width: 80px; height: 60px; object-fit: cover;"></td>
                <td><a href="https://www.youtube.com/watch?v=${esc(video.videoId)}" target="_blank">${esc(video.title)}</a></td>
                <td>${esc(video.channelTitle)}</td>
                <td>${video.viewCount.toLocaleString()}</td>
                <td>${video.likeCount.toLocaleString()}</td>
                <td>${esc(video.duration)}</td>
                <td>${esc(video.skill_name)}</td>
            `;
            tbody.appendChild(row);
        });
        
        previewVideos.appendChild(table);
        
        // Handle select all functionality
        selectAllCheckbox.addEventListener("change", (e) => {
            const checkboxes = table.querySelectorAll(".video_checkbox");
            checkboxes.forEach(cb => cb.checked = e.target.checked);
        });
        
        previewSection.style.display = "block";
        previewSection.scrollIntoView({ behavior: "smooth" });
    }

    function hidePreview() {
        previewSection.style.display = "none";
        previewData = null;
    }

    upsertSelectedBtn.addEventListener("click", async () => {
        const selectedCheckboxes = previewVideos.querySelectorAll(".video_checkbox:checked");
        if (selectedCheckboxes.length === 0) {
            setRunState("Please select at least one video to upsert.", "error");
            return;
        }

        const selectedVideos = Array.from(selectedCheckboxes).map(cb => {
            const index = parseInt(cb.dataset.index);
            return previewData.videos[index];
        });

        upsertSelectedBtn.disabled = true;
        cancelPreviewBtn.disabled = true;
        setRunState("Upserting selected videos into MongoDB...", "running", true);

        try {
            const response = await fetch("/upsert_selected", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ videos: selectedVideos }),
            });
            const result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || "Upsert failed");
            }

            let statusType = "success";
            if (result.summary?.error_count > 0) {
                statusType = "warning";
            }
            setRunState(result.message || "Videos upserted successfully.", statusType);
            renderSummary(result.summary || {}, result.run_id);
            hidePreview();
            await refreshMongoStatus();
        } catch (error) {
            setRunState(`Upsert failed: ${error.message}`, "error");
        } finally {
            upsertSelectedBtn.disabled = false;
            cancelPreviewBtn.disabled = false;
        }
    });

    cancelPreviewBtn.addEventListener("click", () => {
        hidePreview();
        setRunState("Preview cancelled. Ready to run ingestion.", "idle");
        runSummary.innerHTML = "";
    });

    async function bootstrap() {
        if (sectorSelect.value) {
            try {
                await loadSkills(sectorSelect.value, "");
                await loadCompetencies();
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
