(function () {
    const mongoBrowserState = document.getElementById("mongo_browser_state");
    const mongoBrowserResults = document.getElementById("mongo_browser_results");
    const mongoCollectionSelect = document.getElementById("mongo_collection_select");
    const mongoFilterField = document.getElementById("mongo_filter_field");
    const mongoFilterValue = document.getElementById("mongo_filter_value");
    const mongoLimitInput = document.getElementById("mongo_limit");
    const mongoLoadBtn = document.getElementById("mongo_load_btn");
    const mongoPrevBtn = document.getElementById("mongo_prev_btn");
    const mongoNextBtn = document.getElementById("mongo_next_btn");
    let mongoBrowserSkip = 0;
    let mongoBrowserHasMore = false;

    function esc(text) {
        const value = String(text ?? "");
        return value
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function setStatus(el, message, statusType = "idle") {
        el.className = "status";
        if (statusType === "running") el.classList.add("running");
        if (statusType === "success") el.classList.add("success");
        if (statusType === "error") el.classList.add("error");
        if (statusType === "warning") el.classList.add("warning");
        el.textContent = message;
    }

    function setMongoBrowserState(message, statusType = "idle") {
        setStatus(mongoBrowserState, message, statusType);
    }

    function youtubeWatchUrl(videoId) {
        return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
    }

    function renderMongoDocs(docs) {
        if (!docs.length) {
            mongoBrowserResults.textContent = "No documents found.";
            return;
        }
        mongoBrowserResults.innerHTML = docs
            .map((doc) => {
                const videoId = doc.videoId || "";
                const title = doc.title || "-";
                const sector = doc.sector || "-";
                const skillName = doc.skill_name || "-";
                const ingested = doc.ingested_timing || "-";
                const publishedAt = doc.publishedAt || "-";
                const actions = videoId
                    ? `
                        <div class="actions">
                            <button type="button" data-action="copy-video-id" data-video-id="${esc(videoId)}">Copy ID</button>
                            <a class="link-button" href="${esc(youtubeWatchUrl(videoId))}" target="_blank" rel="noopener noreferrer">Open on YouTube</a>
                        </div>
                    `
                    : "";
                return `
                    <article class="doc-card">
                        <div class="doc-meta">
                            <div><strong>Video ID</strong><div class="mono">${esc(videoId || "-")}</div></div>
                            <div><strong>Skill</strong><div>${esc(skillName)}</div></div>
                            <div><strong>Sector</strong><div>${esc(sector)}</div></div>
                            <div><strong>Published</strong><div>${esc(publishedAt)}</div></div>
                            <div><strong>Ingested</strong><div>${esc(ingested)}</div></div>
                        </div>
                        <div class="doc-title"><strong>Title</strong><div>${esc(title)}</div></div>
                        ${actions}
                        <details>
                            <summary>Raw JSON</summary>
                            <pre>${esc(JSON.stringify(doc, null, 2))}</pre>
                        </details>
                    </article>
                `;
            })
            .join("");
    }



    function buildMongoQueryParams() {
        const params = new URLSearchParams();
        params.set("collection", mongoCollectionSelect.value || "videos");
        params.set("limit", mongoLimitInput.value || "20");
        params.set("skip", String(mongoBrowserSkip));
        const filterField = mongoFilterField.value || "";
        const filterValue = mongoFilterValue.value || "";
        if (filterField && filterValue) {
            params.set("filter_field", filterField);
            params.set("filter_value", filterValue);
        }
        return params;
    }

    async function loadMongoCollections() {
        try {
            const response = await fetch("/mongo_collections");
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Unable to load collections");
            }
            const collections = payload.collections || [];
            mongoCollectionSelect.innerHTML = collections
                .map((name) => `<option value="${esc(name)}">${esc(name)}</option>`)
                .join("");
            if (!collections.length) {
                setMongoBrowserState("No collections found.", "warning");
                mongoBrowserResults.textContent = "";
                return;
            }
            mongoCollectionSelect.value = collections.includes("videos")
                ? "videos"
                : collections[0];
            setMongoBrowserState("Collections loaded.", "success");
        } catch (error) {
            setMongoBrowserState(`Collection load failed: ${error.message}`, "error");
        }
    }

    async function loadMongoDocuments() {
        if (!mongoCollectionSelect.value) {
            setMongoBrowserState("Select a collection first.", "warning");
            mongoBrowserResults.textContent = "";
            return;
        }
        setMongoBrowserState("Loading documents...", "running");
        try {
            const params = buildMongoQueryParams();
            const response = await fetch(`/mongo_documents?${params.toString()}`);
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Unable to load documents");
            }
            const docs = payload.documents || [];
            mongoBrowserHasMore = Boolean(payload.has_more);
            const limit = payload.limit || docs.length;
            const skip = payload.skip || 0;
            const start = docs.length ? skip + 1 : 0;
            const end = docs.length ? skip + docs.length : 0;
            setMongoBrowserState(
                docs.length
                    ? `Showing ${start}-${end} (limit ${limit}).`
                    : "No documents found.",
                docs.length ? "success" : "warning"
            );
            renderMongoDocs(docs);
            mongoPrevBtn.disabled = skip <= 0;
            mongoNextBtn.disabled = !mongoBrowserHasMore;
        } catch (error) {
            setMongoBrowserState(`Document load failed: ${error.message}`, "error");
        }
    }

    async function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return;
        }
        const temp = document.createElement("textarea");
        temp.value = text;
        document.body.appendChild(temp);
        temp.select();
        document.execCommand("copy");
        document.body.removeChild(temp);
    }

    function resetMongoPagination() {
        mongoBrowserSkip = 0;
    }

    mongoLoadBtn.addEventListener("click", () => {
        resetMongoPagination();
        loadMongoDocuments();
    });

    mongoPrevBtn.addEventListener("click", () => {
        const limit = parseInt(mongoLimitInput.value || "20", 10);
        mongoBrowserSkip = Math.max(0, mongoBrowserSkip - limit);
        loadMongoDocuments();
    });

    mongoNextBtn.addEventListener("click", () => {
        if (!mongoBrowserHasMore) return;
        const limit = parseInt(mongoLimitInput.value || "20", 10);
        mongoBrowserSkip += limit;
        loadMongoDocuments();
    });

    mongoCollectionSelect.addEventListener("change", () => {
        resetMongoPagination();
        loadMongoDocuments();
    });

    mongoLimitInput.addEventListener("change", () => {
        resetMongoPagination();
        loadMongoDocuments();
    });

    mongoFilterValue.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        resetMongoPagination();
        loadMongoDocuments();
    });

    mongoBrowserResults.addEventListener("click", async (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;
        const action = button.getAttribute("data-action");
        const videoId = (button.getAttribute("data-video-id") || "").trim();
        if (!videoId) return;

        if (action === "copy-video-id") {
            try {
                await copyToClipboard(videoId);
                setMongoBrowserState(`Copied ${videoId}.`, "success");
            } catch (_error) {
                setMongoBrowserState("Unable to copy video ID.", "error");
            }
        }
    });

    async function bootstrap() {
        await loadMongoCollections();
        await loadMongoDocuments();
    }

    bootstrap();
})();
