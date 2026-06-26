(function () {
    // State management
    const state = {
        selectedSearchType: "title",
        selectedVideo: null,
        currentMappings: [],
        pendingRemove: new Map(),
        pendingAdd: new Map(),
        modalSelection: new Set(),
        selectedProficiency: null,
        availableCompetencies: { knowledge_items: [], ability_items: [] },
    };

    // DOM Elements
    const videoSearchInput = document.getElementById("video_search_input");
    const videoSearchBtn = document.getElementById("video_search_btn");
    const searchStatus = document.getElementById("search_status");
    const videoList = document.getElementById("video_list");
    const searchTypeBtns = document.querySelectorAll(".search-type-btn");

    const mappingStatus = document.getElementById("mapping_status");
    const videoDetailsContainer = document.getElementById("video_details_container");
    const noSelectionMessage = document.getElementById("no_selection_message");
    const currentMappingsList = document.getElementById("current_mappings_list");
    const addMappingsBtn = document.getElementById("add_mappings_btn");
    const saveChangesBtn = document.getElementById("save_changes_btn");
    const resetChangesBtn = document.getElementById("reset_changes_btn");

    const competencyModal = document.getElementById("competency_modal");
    const modalCloseBtn = document.getElementById("modal_close_btn");
    const modalCancelBtn = document.getElementById("modal_cancel_btn");
    const modalAddBtn = document.getElementById("modal_add_btn");
    const proficiencyOptions = document.getElementById("proficiency_options");
    const proficiencyDescription = document.getElementById("proficiency_description");
    const knowledgeSection = document.getElementById("knowledge_section");
    const knowledgeItems = document.getElementById("knowledge_items");
    const abilitySection = document.getElementById("ability_section");
    const abilityItems = document.getElementById("ability_items");
    const selectedCount = document.getElementById("selected_count");
    const modalStatus = document.getElementById("modal_status");

    // Utility functions
    function esc(text) {
        const value = String(text ?? "");
        return value
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function setStatus(element, message, statusType = "idle", showSpinner = false) {
        element.className = "status";
        if (statusType === "running") element.classList.add("running");
        if (statusType === "success") element.classList.add("success");
        if (statusType === "error") element.classList.add("error");
        if (statusType === "warning") element.classList.add("warning");
        element.innerHTML = showSpinner
            ? `<span class="spinner"></span>${esc(message)}`
            : esc(message);
    }

    function mappingKey(mapping) {
        return `${mapping.proficiency_level || ""}\u0000${mapping.competency || ""}`;
    }

    function youtubeWatchUrl(videoId) {
        return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
    }

    function setProficiencyDescription(level, description) {
        const text = (description || "").trim();
        if (!text) {
            proficiencyDescription.classList.remove("visible");
            proficiencyDescription.innerHTML = "";
            return;
        }

        proficiencyDescription.classList.add("visible");
        proficiencyDescription.innerHTML = `
            <strong>Level ${esc(level)} Description</strong>
            <div>${esc(text)}</div>
        `;
    }

    // Search type toggle
    searchTypeBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            searchTypeBtns.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.selectedSearchType = btn.dataset.type;
        });
    });

    // Search videos
    async function searchVideos() {
        const query = videoSearchInput.value.trim();
        if (!query) {
            setStatus(searchStatus, "Please enter a search term.", "warning");
            return;
        }

        setStatus(searchStatus, "Searching videos...", "running", true);
        videoList.innerHTML = "";

        try {
            const response = await fetch("/api/public/multi-label/search-videos", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query: query,
                    limit: 20,
                    search_type: state.selectedSearchType,
                }),
            });

            if (!response.ok) {
                const error = await response.json();
                setStatus(searchStatus, `Error: ${error.error || error.detail || "Search failed"}`, "error");
                return;
            }

            const data = await response.json();
            if (data.count === 0) {
                setStatus(searchStatus, "No videos found.", "warning");
                videoList.innerHTML = '<div class="empty-mappings">No results found.</div>';
                return;
            }

            renderVideoList(data.results);
            setStatus(searchStatus, `Found ${data.count} video(s).`, "success");
        } catch (error) {
            setStatus(searchStatus, `Error: ${error.message}`, "error");
        }
    }

    function renderVideoList(videos) {
        videoList.innerHTML = "";
        videos.forEach((video) => {
            const item = document.createElement("div");
            item.className = `video-item ${state.selectedVideo?.video_id === video.video_id ? "selected" : ""}`;

            const mappingCount = video.current_mappings.length;
            const mappingText = mappingCount === 0 
                ? "No mappings" 
                : `${mappingCount} mapping${mappingCount !== 1 ? "s" : ""}`;
            const youtubeAction = video.video_id
                ? `
                    <a class="video-youtube-link" href="${esc(youtubeWatchUrl(video.video_id))}" target="_blank" rel="noopener noreferrer">
                        Open on YouTube
                    </a>
                `
                : "";

            item.innerHTML = `
                <div class="video-item-title">${esc(video.title)}</div>
                <div class="video-item-meta">
                    <span><strong>Skill:</strong> ${esc(video.skill_name)}</span>
                    <span><strong>Level:</strong> ${esc(video.proficiency_level || "-")}</span>
                    <span><strong>Sector:</strong> ${esc(video.sector)}</span>
                </div>
                <div class="video-item-footer">
                    <div class="video-item-mappings">${esc(mappingText)}</div>
                    ${youtubeAction}
                </div>
            `;

            item.addEventListener("click", () => selectVideo(video, item));
            const youtubeLink = item.querySelector(".video-youtube-link");
            if (youtubeLink) {
                youtubeLink.addEventListener("click", (event) => {
                    event.stopPropagation();
                });
            }
            videoList.appendChild(item);
        });
    }

    function selectVideo(video, selectedItem) {
        // Update state
        state.selectedVideo = video;
        state.currentMappings = JSON.parse(JSON.stringify(video.current_mappings));
        state.pendingRemove.clear();
        state.pendingAdd.clear();
        state.modalSelection.clear();

        // Update UI
        document.querySelectorAll(".video-item").forEach((item) => {
            item.classList.remove("selected");
        });
        selectedItem.classList.add("selected");

        // Show details
        videoDetailsContainer.style.display = "block";
        noSelectionMessage.style.display = "none";

        document.getElementById("details_video_id").textContent = video.video_id;
        document.getElementById("details_title").textContent = video.title;
        document.getElementById("details_sector").textContent = video.sector;
        document.getElementById("details_skill").textContent = video.skill_name;

        renderCurrentMappings();
        updateSaveButtonState();
        setStatus(mappingStatus, "Video selected. Ready to edit.", "idle");
    }

    function renderCurrentMappings() {
        currentMappingsList.innerHTML = "";

        if (state.currentMappings.length === 0) {
            currentMappingsList.innerHTML = '<div class="empty-mappings">No mappings yet. Add one to get started.</div>';
            return;
        }

        state.currentMappings.forEach((mapping, index) => {
            const key = mappingKey(mapping);
            const isPendingRemove = state.pendingRemove.has(key);
            const item = document.createElement("div");
            item.className = `mapping-item ${isPendingRemove ? "pending-remove" : ""}`;
            const proficiencyDescription = (mapping.proficiency_description || "").trim();
            const proficiencyText = proficiencyDescription
                ? `Level ${mapping.proficiency_level}: ${proficiencyDescription}`
                : `Level ${mapping.proficiency_level}`;

            item.innerHTML = `
                <div class="mapping-content">
                    <div class="mapping-competency">${esc(mapping.competency)}</div>
                    <div class="mapping-level">${esc(proficiencyText)}</div>
                </div>
                <button class="mapping-remove-btn" data-index="${index}">
                    ${isPendingRemove ? "Undo Remove" : "Remove"}
                </button>
            `;

            const removeBtn = item.querySelector(".mapping-remove-btn");
            removeBtn.addEventListener("click", () => {
                if (isPendingRemove) {
                    state.pendingRemove.delete(key);
                } else if (state.pendingAdd.has(key)) {
                    state.pendingAdd.delete(key);
                    state.currentMappings.splice(index, 1);
                } else {
                    state.pendingRemove.set(key, mapping);
                }
                renderCurrentMappings();
                updateSaveButtonState();
            });

            currentMappingsList.appendChild(item);
        });
    }

    function updateSaveButtonState() {
        const hasChanges = state.pendingRemove.size > 0 || state.pendingAdd.size > 0;
        saveChangesBtn.disabled = !hasChanges;
    }

    // Add mappings modal
    addMappingsBtn.addEventListener("click", () => {
        if (!state.selectedVideo) return;
        openCompetencyModal();
    });

    function openCompetencyModal() {
        state.selectedProficiency = null;
        state.modalSelection.clear();
        proficiencyOptions.innerHTML = "";
        knowledgeItems.innerHTML = "";
        abilityItems.innerHTML = "";
        selectedCount.textContent = "0";
        modalAddBtn.disabled = true;
        setProficiencyDescription("", "");

        // Get available proficiency levels
        const commonLevels = ["1", "2", "3", "4", "5", "6", "Basic", "Intermediate", "Advanced"];
        commonLevels.forEach((level) => {
            const btn = document.createElement("button");
            btn.className = "proficiency-option";
            btn.textContent = `Level ${level}`;
            btn.addEventListener("click", () => selectProficiency(level));
            proficiencyOptions.appendChild(btn);
        });

        competencyModal.classList.add("open");
        setStatus(modalStatus, "Select a proficiency level to view competencies.", "idle");
    }

    async function selectProficiency(level) {
        if (!state.selectedVideo) return;

        state.selectedProficiency = level;
        state.modalSelection.clear();
        updateSelectedCount();
        setProficiencyDescription("", "");

        // Update button UI
        document.querySelectorAll(".proficiency-option").forEach((btn) => {
            btn.classList.toggle("selected", btn.textContent === `Level ${level}`);
        });

        const sector = (state.selectedVideo.sector || "").trim();
        const skill = (state.selectedVideo.skill_name || "").trim();
        if (!sector || !skill) {
            setStatus(modalStatus, "Error: selected video is missing sector or skill metadata.", "error");
            return;
        }

        setStatus(modalStatus, "Loading competencies...", "running", true);
        knowledgeItems.innerHTML = "";
        abilityItems.innerHTML = "";

        try {
            const params = new URLSearchParams({
                sector,
                skill,
                proficiency_level: level,
            });
            const response = await fetch(`/api/public/multi-label/competencies?${params.toString()}`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
            });

            if (!response.ok) {
                const error = await response.json();
                setStatus(modalStatus, `Error: ${error.error || error.detail || "Failed to load competencies"}`, "error");
                return;
            }

            const data = await response.json();
            state.availableCompetencies = data;
            setProficiencyDescription(level, data.proficiency_description);

            renderCompetencyItems(data);
            setStatus(modalStatus, "Select competencies to add.", "success");
        } catch (error) {
            setStatus(modalStatus, `Error: ${error.message}`, "error");
        }

        function renderCompetencyItems(data) {
            // Knowledge items
            if (data.knowledge_items.length > 0) {
                knowledgeItems.innerHTML = "";
                knowledgeSection.style.display = "block";
                const toggle = knowledgeSection.querySelector(".competency-section-toggle");
                toggle.textContent = "−";

                data.knowledge_items.forEach((item) => {
                    const label = document.createElement("label");
                    label.className = "competency-item";
                    label.innerHTML = `
                        <input type="checkbox" value="${esc(item)}" class="competency-checkbox" />
                        <span>${esc(item)}</span>
                    `;

                    const checkbox = label.querySelector("input");
                    checkbox.addEventListener("change", () => {
                        if (checkbox.checked) {
                            state.modalSelection.add(item);
                        } else {
                            state.modalSelection.delete(item);
                        }
                        updateSelectedCount();
                    });

                    knowledgeItems.appendChild(label);
                });
            } else {
                knowledgeSection.style.display = "none";
            }

            // Ability items
            if (data.ability_items.length > 0) {
                abilityItems.innerHTML = "";
                abilitySection.style.display = "block";
                const toggle = abilitySection.querySelector(".competency-section-toggle");
                toggle.textContent = "−";

                data.ability_items.forEach((item) => {
                    const label = document.createElement("label");
                    label.className = "competency-item";
                    label.innerHTML = `
                        <input type="checkbox" value="${esc(item)}" class="competency-checkbox" />
                        <span>${esc(item)}</span>
                    `;

                    const checkbox = label.querySelector("input");
                    checkbox.addEventListener("change", () => {
                        if (checkbox.checked) {
                            state.modalSelection.add(item);
                        } else {
                            state.modalSelection.delete(item);
                        }
                        updateSelectedCount();
                    });

                    abilityItems.appendChild(label);
                });
            } else {
                abilitySection.style.display = "none";
            }
        }
    }

    function updateSelectedCount() {
        selectedCount.textContent = state.modalSelection.size;
        modalAddBtn.disabled = state.modalSelection.size === 0;
    }

    // Toggle sections in modal
    document.querySelectorAll(".competency-section-title").forEach((title) => {
        title.addEventListener("click", () => {
            const section = title.closest(".competency-section");
            const itemsDiv = section.querySelector(".competency-items");
            const toggle = section.querySelector(".competency-section-toggle");

            if (itemsDiv.style.display === "none") {
                itemsDiv.style.display = "flex";
                toggle.textContent = "−";
            } else {
                itemsDiv.style.display = "none";
                toggle.textContent = "+";
            }
        });
    });

    modalAddBtn.addEventListener("click", () => {
        if (state.modalSelection.size === 0 || !state.selectedProficiency) return;

        // Add selected competencies to pending add
        let addedCount = 0;
        state.modalSelection.forEach((competency) => {
            const alreadyMapped = state.currentMappings.some(
                (mapping) =>
                    mapping.competency === competency &&
                    String(mapping.proficiency_level) === String(state.selectedProficiency)
            );
            if (alreadyMapped) return;

            const mapping = {
                competency: competency,
                item_type: competency.split(":")[0].trim().toLowerCase(),
                proficiency_level: state.selectedProficiency,
                proficiency_description: state.availableCompetencies.proficiency_description || "",
            };
            state.currentMappings.push(mapping);
            state.pendingAdd.set(mappingKey(mapping), mapping);
            addedCount += 1;
        });

        closeCompetencyModal();
        renderCurrentMappings();
        updateSaveButtonState();
        setStatus(mappingStatus, `Added ${addedCount} competency(ies).`, "success");
    });

    function closeCompetencyModal() {
        competencyModal.classList.remove("open");
        state.selectedProficiency = null;
        state.modalSelection.clear();
    }

    modalCloseBtn.addEventListener("click", closeCompetencyModal);
    modalCancelBtn.addEventListener("click", closeCompetencyModal);
    competencyModal.addEventListener("click", (event) => {
        if (event.target === competencyModal) {
            closeCompetencyModal();
        }
    });

    // Save and reset
    saveChangesBtn.addEventListener("click", async () => {
        if (!state.selectedVideo) return;

        const mappingsToAdd = Array.from(state.pendingAdd.values());
        const mappingsToRemove = Array.from(state.pendingRemove.values());

        setStatus(mappingStatus, "Saving changes...", "running", true);
        saveChangesBtn.disabled = true;

        try {
            const response = await fetch("/api/public/multi-label/update-video", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    video_id: state.selectedVideo.video_id,
                    skill_name: state.selectedVideo.skill_name,
                    sector: state.selectedVideo.sector,
                    competency: state.selectedVideo.competency,
                    proficiency_level: state.selectedVideo.proficiency_level,
                    mappings_to_add: mappingsToAdd,
                    mappings_to_remove: mappingsToRemove,
                }),
            });

            if (!response.ok) {
                const error = await response.json();
                setStatus(mappingStatus, `Error: ${error.error || error.detail || "Save failed"}`, "error");
                saveChangesBtn.disabled = false;
                return;
            }

            const data = await response.json();

            const removedKeys = new Set(state.pendingRemove.keys());
            state.currentMappings = state.currentMappings.filter(
                (mapping) => !removedKeys.has(mappingKey(mapping))
            );
            state.pendingRemove.clear();
            state.pendingAdd.clear();
            state.selectedVideo.current_mappings = JSON.parse(JSON.stringify(state.currentMappings));

            const selectedCountLabel = videoList.querySelector(".video-item.selected .video-item-mappings");
            if (selectedCountLabel) {
                const total = Number(data.total_mappings ?? state.currentMappings.length);
                selectedCountLabel.textContent = total === 0
                    ? "No mappings"
                    : `${total} mapping${total !== 1 ? "s" : ""}`;
            }

            renderCurrentMappings();
            updateSaveButtonState();
            setStatus(
                mappingStatus,
                `Saved. Added ${data.added || 0}, removed ${data.removed || 0}. Video now has ${data.total_mappings} mapping(s).`,
                "success"
            );
        } catch (error) {
            setStatus(mappingStatus, `Error: ${error.message}`, "error");
        } finally {
            saveChangesBtn.disabled = false;
        }
    });

    resetChangesBtn.addEventListener("click", () => {
        if (!state.selectedVideo) return;

        state.currentMappings = JSON.parse(
            JSON.stringify(state.selectedVideo.current_mappings)
        );
        state.pendingRemove.clear();
        state.pendingAdd.clear();

        renderCurrentMappings();
        updateSaveButtonState();
        setStatus(mappingStatus, "Changes reset.", "idle");
    });

    // Search on Enter
    videoSearchInput.addEventListener("keypress", (event) => {
        if (event.key === "Enter") {
            searchVideos();
        }
    });

    videoSearchBtn.addEventListener("click", searchVideos);
})();
