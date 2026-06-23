(function () {
    const statusFilter = document.getElementById("review_status_filter");
    const refreshBtn = document.getElementById("refresh_review_btn");
    const reviewState = document.getElementById("review_state");
    const requestList = document.getElementById("review_requests");
    const detailEmpty = document.getElementById("review_detail_empty");
    const detailPanel = document.getElementById("review_detail");
    const reviewThumb = document.getElementById("review_thumb");
    const reviewStatusChip = document.getElementById("review_status_chip");
    const reviewTitle = document.getElementById("review_title");
    const reviewChannel = document.getElementById("review_channel");
    const reviewQuery = document.getElementById("review_query");
    const suggestedMappings = document.getElementById("suggested_mappings");
    const copySuggestionsBtn = document.getElementById("copy_suggestions_btn");
    const mappingRows = document.getElementById("mapping_rows");
    const addMappingBtn = document.getElementById("add_mapping_btn");
    const saveMappingBtn = document.getElementById("save_mapping_btn");
    const approveMappingBtn = document.getElementById("approve_mapping_btn");
    const rejectMappingBtn = document.getElementById("reject_mapping_btn");
    const reviewerName = document.getElementById("reviewer_name");
    const rejectReason = document.getElementById("reject_reason");
    const sectors = Array.isArray(window.YTA_SECTORS) ? window.YTA_SECTORS : [];

    let selectedRequest = null;
    let queue = [];
    let busy = false;
    const skillsCache = new Map();
    const levelsCache = new Map();
    const competenciesCache = new Map();

    function esc(text) {
        return String(text ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok === false) {
            throw new Error(payload.error || payload.detail || response.statusText);
        }
        return payload;
    }

    function setState(message, tone = "") {
        reviewState.className = "status";
        if (tone) {
            reviewState.classList.add(tone);
        }
        reviewState.textContent = message;
    }

    function selectedStatus() {
        return String(selectedRequest?.review_status || "pending").toLowerCase();
    }

    function updateActionState() {
        const status = selectedStatus();
        const isApproved = status === "approved";
        const isRejected = status === "rejected";
        const hasSelection = Boolean(selectedRequest);

        saveMappingBtn.hidden = isApproved || isRejected;
        approveMappingBtn.hidden = isApproved || isRejected;
        addMappingBtn.hidden = isApproved || isRejected;
        rejectReason.closest(".field").hidden = isRejected;

        rejectMappingBtn.textContent = isApproved
            ? "Unpublish"
            : isRejected
                ? "Reopen"
                : "Reject";
        rejectMappingBtn.classList.toggle("danger-button", !isRejected);

        copySuggestionsBtn.disabled = busy || !selectedRequest?.suggested_mappings?.length || isApproved || isRejected;
        addMappingBtn.disabled = busy || !hasSelection || isApproved || isRejected;
        saveMappingBtn.disabled = busy || !hasSelection || isApproved || isRejected;
        approveMappingBtn.disabled = busy || !hasSelection || isApproved || isRejected;
        rejectMappingBtn.disabled = busy || !hasSelection;
    }

    function setBusy(nextBusy) {
        busy = nextBusy;
        refreshBtn.disabled = busy;
        updateActionState();
    }

    function emptyMapping() {
        return {
            sector: sectors[0] || "",
            skill: "",
            proficiency_level: "",
            proficiency_description: "",
            item_type: "knowledge",
            competency: "",
            confidence: 0,
            source: "admin",
        };
    }

    function normalizedMapping(mapping) {
        return {
            ...emptyMapping(),
            ...(mapping || {}),
            skill: mapping?.skill || mapping?.skill_name || "",
        };
    }

    function renderQueue() {
        requestList.innerHTML = "";
        if (!queue.length) {
            requestList.innerHTML = `<div class="empty-panel">No ${esc(statusFilter.value)} mapping requests found.</div>`;
            return;
        }

        queue.forEach((request) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = `review-request-item ${selectedRequest?.video_id === request.video_id ? "active" : ""}`;
            button.innerHTML = `
                ${request.thumbnail_url ? `<img src="${esc(request.thumbnail_url)}" alt="">` : `<div class="review-thumb-placeholder"></div>`}
                <span>
                    <strong>${esc(request.title || "Untitled video")}</strong>
                    <small>${esc(request.channel_title || "Unknown channel")}</small>
                    <small>${esc(request.review_status || "pending")}</small>
                </span>
            `;
            button.addEventListener("click", () => loadRequest(request.video_id));
            requestList.appendChild(button);
        });
    }

    function mappingSummary(mapping) {
        const parts = [
            mapping.sector,
            mapping.skill,
            mapping.proficiency_level ? `Level ${mapping.proficiency_level}` : "",
            mapping.competency,
        ].filter(Boolean);
        return parts.join(" / ");
    }

    function renderSuggestions() {
        const rows = selectedRequest?.suggested_mappings || [];
        if (!rows.length) {
            const status = selectedRequest?.suggestion_status || "";
            if (status === "queued" || status === "running") {
                suggestedMappings.innerHTML = `<div class="empty-panel">AI suggestion is being prepared. Refresh shortly or add mappings manually below.</div>`;
                return;
            }
            if (status === "failed") {
                const detail = selectedRequest?.suggestion_error ? `: ${selectedRequest.suggestion_error}` : ".";
                suggestedMappings.innerHTML = `<div class="empty-panel">AI suggestion failed${esc(detail)} Add mappings manually below.</div>`;
                return;
            }
            suggestedMappings.innerHTML = `<div class="empty-panel">No AI suggestions were generated.</div>`;
            return;
        }
        suggestedMappings.innerHTML = rows.map((mapping) => `
            <div class="suggested-mapping">
                <strong>${esc(mappingSummary(mapping))}</strong>
                <span>${esc(mapping.proficiency_description || "")}</span>
                <small>${esc(mapping.source || "ai")} confidence ${Number(mapping.confidence || 0).toFixed(2)}</small>
            </div>
        `).join("");
    }

    function sectorOptions(value) {
        const safeValue = String(value || "");
        const values = safeValue && !sectors.includes(safeValue) ? [safeValue, ...sectors] : sectors;
        return values.map((sector) => `<option value="${esc(sector)}" ${sector === safeValue ? "selected" : ""}>${esc(sector)}</option>`).join("");
    }

    function optionList(values, selectedValue, placeholder) {
        const safeSelected = String(selectedValue || "");
        const uniqueValues = [];
        if (safeSelected && !values.includes(safeSelected)) {
            uniqueValues.push(safeSelected);
        }
        values.forEach((value) => {
            const safeValue = String(value || "").trim();
            if (safeValue && !uniqueValues.includes(safeValue)) {
                uniqueValues.push(safeValue);
            }
        });
        return [
            `<option value="">${esc(placeholder)}</option>`,
            ...uniqueValues.map((value) => `<option value="${esc(value)}" ${value === safeSelected ? "selected" : ""}>${esc(value)}</option>`),
        ].join("");
    }

    function normalizeLevelOption(option) {
        if (typeof option === "string") {
            return { level: option, description: "" };
        }
        return {
            level: String(option?.proficiency_level || option?.level || "").trim(),
            description: String(option?.proficiency_description || option?.description || "").trim(),
        };
    }

    async function postLookup(url, body) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            throw new Error(response.statusText);
        }
        return response.json();
    }

    async function getSkillsForSector(sector) {
        const key = String(sector || "");
        if (!key) return [];
        if (!skillsCache.has(key)) {
            skillsCache.set(key, postLookup("/search_skills", { sector: key, search_term: "" }));
        }
        return skillsCache.get(key);
    }

    async function getLevelsForSkill(sector, skill) {
        const key = `${sector}::${skill}`;
        if (!sector || !skill) return [];
        if (!levelsCache.has(key)) {
            levelsCache.set(key, postLookup("/search_proficiency_levels", { sector, skill }));
        }
        return levelsCache.get(key);
    }

    async function getCompetenciesForLevel(sector, skill, proficiencyLevel) {
        const key = `${sector}::${skill}::${proficiencyLevel}`;
        if (!sector || !skill || !proficiencyLevel) return [];
        if (!competenciesCache.has(key)) {
            competenciesCache.set(key, postLookup("/search_competencies", {
                sector,
                skill,
                proficiency_level: proficiencyLevel,
            }));
        }
        return competenciesCache.get(key);
    }

    function setSelectOptions(select, values, selectedValue, placeholder) {
        select.innerHTML = optionList(values, selectedValue, placeholder);
    }

    async function hydrateMappingRow(row, resetFrom = "") {
        const sectorSelect = row.querySelector('[data-field="sector"]');
        const skillSelect = row.querySelector('[data-field="skill"]');
        const levelSelect = row.querySelector('[data-field="proficiency_level"]');
        const typeSelect = row.querySelector('[data-field="item_type"]');
        const competencySelect = row.querySelector('[data-field="competency"]');
        const descriptionField = row.querySelector('[data-field="proficiency_description"]');

        const sector = sectorSelect.value;
        if (resetFrom === "sector") {
            skillSelect.value = "";
            levelSelect.value = "";
            competencySelect.value = "";
            descriptionField.value = "";
        } else if (resetFrom === "skill") {
            levelSelect.value = "";
            competencySelect.value = "";
            descriptionField.value = "";
        } else if (resetFrom === "level") {
            competencySelect.value = "";
        } else if (resetFrom === "type") {
            competencySelect.value = "";
        }

        try {
            skillSelect.disabled = true;
            levelSelect.disabled = true;
            competencySelect.disabled = true;

            const skills = await getSkillsForSector(sector);
            setSelectOptions(skillSelect, skills, skillSelect.value, "Choose skill");
            skillSelect.disabled = false;

            const skill = skillSelect.value;
            const levels = await getLevelsForSkill(sector, skill);
            const normalizedLevels = levels.map(normalizeLevelOption).filter((level) => level.level);
            setSelectOptions(levelSelect, normalizedLevels.map((level) => level.level), levelSelect.value, "Choose level");
            levelSelect.disabled = !skill;

            const selectedLevel = normalizedLevels.find((level) => level.level === levelSelect.value);
            if (selectedLevel && (!descriptionField.value || resetFrom === "level")) {
                descriptionField.value = selectedLevel.description || "";
            }

            const competencies = await getCompetenciesForLevel(sector, skill, levelSelect.value);
            const selectedType = typeSelect.value;
            const filteredCompetencies = competencies.filter((competency) => (
                !selectedType || String(competency).toLowerCase().startsWith(`${selectedType}:`)
            ));
            setSelectOptions(competencySelect, filteredCompetencies, competencySelect.value, "Choose competency");
            competencySelect.disabled = !skill || !levelSelect.value;
        } catch (error) {
            setState(`Unable to load mapping options: ${error.message}`, "error");
        }
    }

    function wireMappingRow(row) {
        row.querySelector('[data-field="sector"]').addEventListener("change", () => hydrateMappingRow(row, "sector"));
        row.querySelector('[data-field="skill"]').addEventListener("change", () => hydrateMappingRow(row, "skill"));
        row.querySelector('[data-field="proficiency_level"]').addEventListener("change", () => hydrateMappingRow(row, "level"));
        row.querySelector('[data-field="item_type"]').addEventListener("change", () => hydrateMappingRow(row, "type"));
    }

    function renderMappingRows(mappings) {
        const rows = mappings.length ? mappings : [emptyMapping()];
        mappingRows.innerHTML = rows.map((mapping, index) => {
            const row = normalizedMapping(mapping);
            return `
                <div class="mapping-row" data-index="${index}">
                    <label>Sector
                        <select data-field="sector">${sectorOptions(row.sector)}</select>
                    </label>
                    <label>Skill
                        <select data-field="skill">${optionList([], row.skill, "Choose skill")}</select>
                    </label>
                    <label>Level
                        <select data-field="proficiency_level">${optionList([], row.proficiency_level, "Choose level")}</select>
                    </label>
                    <label>Type
                        <select data-field="item_type">
                            <option value="knowledge" ${row.item_type === "knowledge" ? "selected" : ""}>knowledge</option>
                            <option value="ability" ${row.item_type === "ability" ? "selected" : ""}>ability</option>
                        </select>
                    </label>
                    <label class="wide">Competency
                        <select data-field="competency">${optionList([], row.competency, "Choose competency")}</select>
                    </label>
                    <label class="wide">Proficiency Description
                        <textarea data-field="proficiency_description" rows="2">${esc(row.proficiency_description)}</textarea>
                    </label>
                    <button type="button" class="remove-mapping-btn" data-remove="${index}">Remove</button>
                </div>
            `;
        }).join("");

        mappingRows.querySelectorAll("[data-remove]").forEach((button) => {
            button.addEventListener("click", () => {
                const current = readMappings();
                current.splice(Number(button.dataset.remove), 1);
                renderMappingRows(current.length ? current : [emptyMapping()]);
            });
        });
        mappingRows.querySelectorAll(".mapping-row").forEach((row) => {
            wireMappingRow(row);
            hydrateMappingRow(row);
        });
    }

    function readMappings() {
        return Array.from(mappingRows.querySelectorAll(".mapping-row")).map((row) => {
            const mapping = emptyMapping();
            row.querySelectorAll("[data-field]").forEach((field) => {
                mapping[field.dataset.field] = field.value.trim();
            });
            mapping.source = "admin";
            return mapping;
        });
    }

    function showRequest(request) {
        selectedRequest = request;
        detailEmpty.hidden = true;
        detailPanel.hidden = false;
        reviewThumb.src = request.thumbnail_url || "";
        reviewThumb.hidden = !request.thumbnail_url;
        reviewStatusChip.textContent = request.review_status || "pending";
        reviewTitle.textContent = request.title || "Untitled video";
        reviewChannel.textContent = request.channel_title || "Unknown channel";
        reviewQuery.textContent = request.search_query ? `Search query: ${request.search_query}` : "";
        renderSuggestions();
        const mappings = request.approved_mappings?.length
            ? request.approved_mappings
            : request.suggested_mappings?.length
                ? request.suggested_mappings
                : [emptyMapping()];
        renderMappingRows(mappings.map(normalizedMapping));
        renderQueue();
        setBusy(false);
    }

    async function loadQueue() {
        setBusy(true);
        setState("Loading review queue...");
        try {
            const params = new URLSearchParams({ status: statusFilter.value, limit: "100" });
            const payload = await fetchJson(`/api/admin/video-mapping-requests?${params.toString()}`, { cache: "no-store" });
            queue = Array.isArray(payload.requests) ? payload.requests : [];
            setState(queue.length ? `${queue.length} mapping request(s) loaded.` : "No mapping requests found.", queue.length ? "success" : "");
            renderQueue();
            return queue;
        } catch (error) {
            queue = [];
            renderQueue();
            setState(`Unable to load review queue: ${error.message}`, "error");
            return [];
        } finally {
            setBusy(false);
        }
    }

    async function loadRequest(videoId) {
        if (!videoId) return;
        setBusy(true);
        setState("Loading video mapping request...");
        try {
            const payload = await fetchJson(`/api/admin/video-mapping-requests/${encodeURIComponent(videoId)}`, { cache: "no-store" });
            showRequest(payload);
            setState("Review loaded. Edit mappings, then approve or reject.", "success");
        } catch (error) {
            setState(`Unable to load mapping request: ${error.message}`, "error");
        } finally {
            setBusy(false);
        }
    }

    async function saveMappings() {
        if (!selectedRequest) return;
        if (selectedStatus() !== "pending") {
            setState("Only pending requests can be edited. Reopen rejected requests first.", "error");
            return;
        }
        setBusy(true);
        setState("Saving mapping edits...");
        try {
            const payload = await fetchJson(`/api/admin/video-mapping-requests/${encodeURIComponent(selectedRequest.video_id)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reviewer: reviewerName.value.trim() || "admin-console", mappings: readMappings() }),
            });
            showRequest(payload);
            setState("Mapping edits saved.", "success");
            await loadQueue();
        } catch (error) {
            setState(`Unable to save mapping edits: ${error.message}`, "error");
        } finally {
            setBusy(false);
        }
    }

    async function approveMappings() {
        if (!selectedRequest) return;
        if (selectedStatus() !== "pending") {
            setState("Only pending requests can be approved. Reopen rejected requests first.", "error");
            return;
        }
        setBusy(true);
        setState("Approving and indexing video mappings...");
        try {
            const payload = await fetchJson(`/api/admin/video-mapping-requests/${encodeURIComponent(selectedRequest.video_id)}/approve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ reviewer: reviewerName.value.trim() || "admin-console", mappings: readMappings() }),
            });
            selectedRequest = null;
            setState(`${payload.message} Indexed ${Number(payload.embedding_indexed || 0)} mapping(s).`, "success");
            const nextRequests = await loadQueue();
            if (nextRequests.length) {
                await loadRequest(nextRequests[0].video_id);
            } else {
                detailPanel.hidden = true;
                detailEmpty.hidden = false;
            }
        } catch (error) {
            setState(`Unable to approve mappings: ${error.message}`, "error");
        } finally {
            setBusy(false);
        }
    }

    async function rejectMapping() {
        if (!selectedRequest) return;
        const status = selectedStatus();
        const action = status === "approved"
            ? "unpublish"
            : status === "rejected"
                ? "reopen"
                : "reject";
        const actionLabel = action === "unpublish"
            ? "Unpublishing video..."
            : action === "reopen"
                ? "Reopening mapping request..."
                : "Rejecting mapping request...";
        setBusy(true);
        setState(actionLabel);
        try {
            const payload = await fetchJson(`/api/admin/video-mapping-requests/${encodeURIComponent(selectedRequest.video_id)}/${action}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    reviewer: reviewerName.value.trim() || "admin-console",
                    reason: rejectReason.value.trim() || (action === "unpublish" ? "Unpublished in admin console." : "Rejected in admin console."),
                }),
            });
            selectedRequest = null;
            const fallbackMessage = action === "unpublish"
                ? "Video unpublished."
                : action === "reopen"
                    ? "Mapping request reopened."
                    : "Mapping request rejected.";
            const detail = action === "unpublish"
                ? ` Removed ${Number(payload.approved_deleted_count || 0)} approved mapping doc(s).`
                : "";
            setState(`${payload.message || fallbackMessage}${detail}`, "success");
            const nextRequests = await loadQueue();
            if (nextRequests.length) {
                await loadRequest(nextRequests[0].video_id);
            } else {
                detailPanel.hidden = true;
                detailEmpty.hidden = false;
            }
        } catch (error) {
            setState(`Unable to reject mapping request: ${error.message}`, "error");
        } finally {
            setBusy(false);
        }
    }

    refreshBtn.addEventListener("click", loadQueue);
    statusFilter.addEventListener("change", loadQueue);
    addMappingBtn.addEventListener("click", () => renderMappingRows([...readMappings(), emptyMapping()]));
    copySuggestionsBtn.addEventListener("click", () => {
        if (selectedRequest?.suggested_mappings?.length) {
            renderMappingRows(selectedRequest.suggested_mappings.map(normalizedMapping));
            setState("AI suggestions copied into the editor.", "success");
        }
    });
    saveMappingBtn.addEventListener("click", saveMappings);
    approveMappingBtn.addEventListener("click", approveMappings);
    rejectMappingBtn.addEventListener("click", rejectMapping);

    loadQueue();
})();
