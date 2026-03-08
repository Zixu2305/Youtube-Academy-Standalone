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
    const videoAgeUnitInput = document.getElementById("video_age_unit");
    const ageDateNote = document.getElementById("age_date_note");
    const additionalQueryInput = document.getElementById("additional_query");
    /* Removed redundant include query inputs as they are now automatic */
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
    let totalPreviewUnits = 0;

    function esc(text) {
        const value = String(text ?? "");
        return value
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function normalizeProficiencyOption(option) {
        if (typeof option === "string") {
            return { level: option, description: "" };
        }
        return {
            level: String(option?.proficiency_level || option?.level || "").trim(),
            description: String(option?.proficiency_description || option?.description || "").trim(),
        };
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

        const adjustedRemaining = quota.remaining_after_run - totalPreviewUnits;
        const msg = `Estimated units: ${quota.estimated_units} / ${quota.daily_limit} (remaining after run: ${adjustedRemaining})`;
        if (adjustedRemaining <= 0 || quota.level === "over_limit") {
            quotaBanner.classList.add("error");
            quotaBanner.textContent = `${msg}. This likely exceeds daily quota.`;
        } else if (adjustedRemaining <= (quota.daily_limit - quota.warning_threshold) || quota.level === "warning") {
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
                selectedProficiency = null;
                selectedCompetency = null;
                selectedRequirement = null;
                loadProficiencyLevels();
                updateQuotaEstimate();
            });

            row.appendChild(skillButton);
            skillsContainer.appendChild(row);
        });
    }

    async function loadCompetencies() {
        competenciesContainer.innerHTML = "";
        selectedCompetency = null;
        if (!selectedSkill || !selectedProficiency) {
            competenciesContainer.textContent = "Select a proficiency level first";
            return;
        }

        try {
            const response = await fetch("/search_competencies", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector: sectorSelect.value, skill: selectedSkill, proficiency_level: selectedProficiency })
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
                    updateQuotaEstimate();
                });

                row.appendChild(compButton);
                competenciesContainer.appendChild(row);
            });
        } catch (error) {
            competenciesContainer.textContent = "Error loading competencies";
        }
    }

    function loadProficiencyDescription(description = "") {
        renderProficiencyDescription(description ? [description] : []);
    }

    function renderProficiencyDescription(descriptions) {
        requirementsContainer.innerHTML = "";
        if (!descriptions.length || !descriptions[0]) {
            requirementsContainer.textContent = "No description available.";
            selectedRequirement = null;
            return;
        }
        const description = descriptions[0];
        selectedRequirement = description;
        const row = document.createElement("div");
        row.className = "requirement-item";
        row.innerHTML = `<div class="requirement-text">${esc(description)}</div>`;
        requirementsContainer.appendChild(row);
    }

    async function loadProficiencyLevels() {
        proficiencyContainer.innerHTML = "";
        competenciesContainer.innerHTML = "";
        requirementsContainer.innerHTML = "";
        selectedProficiency = null;
        selectedCompetency = null;
        selectedRequirement = null;
        if (!selectedSkill) {
            proficiencyContainer.textContent = "Select a skill first";
            return;
        }

        try {
            const response = await fetch("/search_proficiency_levels", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sector: sectorSelect.value, skill: selectedSkill })
            });
            const levels = await response.json();
            if (!levels.length) {
                proficiencyContainer.textContent = "No proficiency levels found.";
                return;
            }
            levels.forEach((option) => {
                const { level, description } = normalizeProficiencyOption(option);
                if (!level) return;
                const row = document.createElement("div");
                row.className = "proficiency-item";

                const levelButton = document.createElement("span");
                levelButton.className = selectedProficiency === level ? "proficiency-name selected" : "proficiency-name";
                levelButton.textContent = description ? `${level} — ${description}` : level;
                levelButton.addEventListener("click", () => {
                    if (selectedProficiency === level) {
                        selectedProficiency = null;
                        selectedRequirement = null;
                        levelButton.className = "proficiency-name";
                    } else {
                        // Clear previous selection
                        const prevSelected = document.querySelector(".proficiency-name.selected");
                        if (prevSelected) {
                            prevSelected.className = "proficiency-name";
                        }
                        selectedProficiency = level;
                        selectedRequirement = description || null;
                        levelButton.className = "proficiency-name selected";
                    }
                    // Clear downstream
                    selectedCompetency = null;
                    if (selectedProficiency) {
                        loadProficiencyDescription(selectedRequirement || "");
                        loadCompetencies();
                    } else {
                        loadProficiencyDescription("");
                    }
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
        // Requirement is auto-loaded by loadProficiencyDescription (called from proficiency click)
        // This function is here for reference but not called directly
        return;
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

    // ── Client-side validation ──────────────────────────────────────
    function clearAllErrors() {
        document.querySelectorAll(".field-error").forEach((el) => {
            el.textContent = "";
        });
        document.querySelectorAll(".constraint-input.invalid").forEach((el) => {
            el.classList.remove("invalid");
        });
    }

    function setFieldError(fieldId, message) {
        const errSpan = document.getElementById("err_" + fieldId);
        const input = document.getElementById(fieldId);
        if (errSpan) errSpan.textContent = message;
        if (input) input.classList.add("invalid");
    }

    function validateConstraints() {
        clearAllErrors();
        const errors = [];

        // search_max_results
        const smr = parseInt(searchMaxResultsInput.value, 10);
        if (isNaN(smr) || smr < 1 || smr > 50) {
            errors.push("search_max_results");
            setFieldError("search_max_results", "Must be between 1 and 50.");
        }

        // published_after
        const paVal = publishedAfterInput.value;
        if (paVal) {
            const paDate = new Date(paVal);
            if (isNaN(paDate.getTime())) {
                errors.push("published_after");
                setFieldError("published_after", "Invalid date.");
            } else if (paDate > new Date()) {
                errors.push("published_after");
                setFieldError("published_after", "Cannot be in the future.");
            }
        }

        // max_video_age
        const age = parseInt(maxVideoAgeInput.value, 10);
        if (isNaN(age) || age < 0) {
            errors.push("max_video_age");
            setFieldError("max_video_age", "Must be 0 or a positive number.");
        }

        // conflict: both published_after and video_age
        if (paVal && age > 0) {
            errors.push("max_video_age");
            setFieldError("max_video_age", "Use either Published After or Video Age, not both.");
            setFieldError("published_after", "Use either Published After or Video Age, not both.");
        }

        // min_view_count
        const mvc = parseInt(minViewCountInput.value, 10);
        if (isNaN(mvc) || mvc < 0) {
            errors.push("min_view_count");
            setFieldError("min_view_count", "Cannot be negative.");
        }

        // min_like_count
        const mlc = parseInt(minLikeCountInput.value, 10);
        if (isNaN(mlc) || mlc < 0) {
            errors.push("min_like_count");
            setFieldError("min_like_count", "Cannot be negative.");
        }

        // min_video_length
        const minVL = parseInt(minVideoLengthInput.value, 10);
        if (isNaN(minVL) || minVL < 0) {
            errors.push("min_video_length");
            setFieldError("min_video_length", "Cannot be negative.");
        }

        // max_video_length
        const maxVL = parseInt(maxVideoLengthInput.value, 10);
        if (isNaN(maxVL) || maxVL < 0) {
            errors.push("max_video_length");
            setFieldError("max_video_length", "Cannot be negative.");
        }

        // min > max video length
        if (minVL > 0 && maxVL > 0 && minVL > maxVL) {
            errors.push("min_video_length");
            setFieldError("min_video_length", "Cannot exceed maximum.");
            setFieldError("max_video_length", "Must be ≥ minimum.");
        }

        // min_comment_count
        const mcc = parseInt(minCommentCountInput.value, 10);
        if (isNaN(mcc) || mcc < 0) {
            errors.push("min_comment_count");
            setFieldError("min_comment_count", "Cannot be negative.");
        }

        return errors;
    }

    function updateAgeDateNote() {
        const age = parseInt(maxVideoAgeInput.value, 10);
        const unit = videoAgeUnitInput.value;
        const paVal = publishedAfterInput.value;
        if (age > 0 && !paVal) {
            const multipliers = { days: 1, weeks: 7, months: 30, years: 365 };
            const totalDays = age * (multipliers[unit] || 1);
            const cutoff = new Date();
            cutoff.setDate(cutoff.getDate() - totalDays);
            ageDateNote.textContent = `Videos published after ≈ ${cutoff.toISOString().slice(0, 10)}`;
            ageDateNote.style.display = "block";
        } else {
            ageDateNote.style.display = "none";
        }
    }

    function syncTimeConstraints() {
        const paHasValue = publishedAfterInput.value.trim() !== "";
        const ageHasValue = !isNaN(parseInt(maxVideoAgeInput.value, 10)) && parseInt(maxVideoAgeInput.value, 10) > 0;

        if (paHasValue) {
            // Published After is active → disable video age
            maxVideoAgeInput.disabled = true;
            videoAgeUnitInput.disabled = true;
            maxVideoAgeInput.classList.add("disabled-field");
            videoAgeUnitInput.classList.add("disabled-field");
            publishedAfterInput.disabled = false;
            publishedAfterInput.classList.remove("disabled-field");
        } else if (ageHasValue) {
            // Video Age is active → disable published after
            publishedAfterInput.disabled = true;
            publishedAfterInput.classList.add("disabled-field");
            maxVideoAgeInput.disabled = false;
            videoAgeUnitInput.disabled = false;
            maxVideoAgeInput.classList.remove("disabled-field");
            videoAgeUnitInput.classList.remove("disabled-field");
        } else {
            // Neither has a value → enable both
            publishedAfterInput.disabled = false;
            publishedAfterInput.classList.remove("disabled-field");
            maxVideoAgeInput.disabled = false;
            videoAgeUnitInput.disabled = false;
            maxVideoAgeInput.classList.remove("disabled-field");
            videoAgeUnitInput.classList.remove("disabled-field");
        }

        updateAgeDateNote();
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
            published_after: publishedAfterInput.value,
            max_video_age: maxVideoAgeInput.value,
            video_age_unit: videoAgeUnitInput.value,
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
                    (row) => {
                        const sector = row.sector || "-";
                        const skills = row.skill_name || "-";
                        const competency = row.competency || "-";
                        const proficiency = row.proficiency || "-";
                        const requirement = row.requirement || "-";
                        const skillDetails = `<strong>Sector:</strong> ${esc(sector)}, <strong>Skills:</strong> ${esc(skills)}, <strong>Competency:</strong> ${esc(competency)}, <strong>Proficiency:</strong> ${esc(proficiency)}, <strong>Requirement:</strong> ${esc(requirement)}`;
                        return `
                <tr>
                    <td>${esc(row.ingested_timing || "-")}</td>
                    <td>${skillDetails}</td>
                    <td class="mono">${esc(row.videoId || "-")}</td>
                    <td>${esc(row.title || "-")}</td>
                </tr>
            `;
                    }
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
                const sector = run.params?.sector || "-";
                const skills = (run.params?.selected_skills || []).join(", ") || "-";
                const competency = run.params?.competency || "-";
                const proficiency = run.params?.proficiency || "-";
                const requirement = run.params?.requirement || "-";
                const skillsDetails = `<strong>Sector:</strong> ${esc(sector)}, <strong>Skills:</strong> ${esc(skills)}, <strong>Competency:</strong> ${esc(competency)}, <strong>Proficiency:</strong> ${esc(proficiency)}, <strong>Requirement:</strong> ${esc(requirement)}`;
                const upserts = run.params?.videos_to_upsert ?? run.summary?.upserts_attempted ?? "-";
                return `
                    <tr>
                        <td>${esc(started)}</td>
                        <td>${esc(status)}</td>
                        <td>${skillsDetails}</td>
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

        // Run client-side validation first
        const validationErrors = validateConstraints();
        if (validationErrors.length > 0) {
            setRunState("Fix the highlighted constraint errors before submitting.", "error");
            return;
        }

        if (!selectedSkill || !selectedProficiency || !selectedRequirement || !selectedCompetency) {
            setRunState("Select all required fields: Skill, Proficiency Level, Requirement, and Competency.", "error");
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
            totalPreviewUnits += (result.quota && result.quota.estimated_units) ? result.quota.estimated_units : 0;
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
        skillsContainer.innerHTML = "";
        competenciesContainer.innerHTML = "";
        proficiencyContainer.innerHTML = "";
        requirementsContainer.innerHTML = "";
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
        maxVideoAgeInput,
        videoAgeUnitInput,
        additionalQueryInput,
        /* Removed automatic query inputs */
    ].forEach((el) => el.addEventListener("input", () => {
        updateQuotaEstimate();
    }));

    [maxVideoAgeInput, videoAgeUnitInput, publishedAfterInput].forEach((el) =>
        el.addEventListener("change", syncTimeConstraints)
    );

    /* Removed change listeners for removed query inputs */

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
                body: JSON.stringify({ videos: selectedVideos, payload: collectPayload() }),
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
