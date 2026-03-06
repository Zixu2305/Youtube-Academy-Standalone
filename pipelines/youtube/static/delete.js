(function () {
    const questionIdInput = document.getElementById("question_id_input");
    const deleteQuestionBtn = document.getElementById("delete_question_btn");
    const deleteStatus = document.getElementById("delete_status");

    const videoIdInput = document.getElementById("video_id_input");
    const deleteVideoBtn = document.getElementById("delete_video_btn");
    const deleteVideoStatus = document.getElementById("delete_video_status");

    async function deleteQuestion() {
        const questionId = questionIdInput.value.trim();
        console.log("Delete question button clicked. Question ID:", questionId);
        
        if (!questionId) {
            deleteStatus.textContent = "⚠ Please enter a Question ID";
            deleteStatus.style.color = "#ff9800";
            deleteStatus.style.display = "block";
            console.warn("No question ID provided");
            return;
        }

        if (questionId.length !== 24) {
            deleteStatus.textContent = "⚠ Question ID must be 24 characters (MongoDB ObjectId)";
            deleteStatus.style.color = "#ff9800";
            deleteStatus.style.display = "block";
            console.warn("Invalid question ID format. Length:", questionId.length);
            return;
        }

        deleteQuestionBtn.disabled = true;
        deleteStatus.textContent = "Deleting...";
        deleteStatus.style.color = "#2196f3";
        deleteStatus.style.display = "block";

        try {
            console.log("Sending delete request to /delete_question");
            const res = await fetch("/delete_question", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question_id: questionId }),
            });

            console.log("Response status:", res.status);
            const result = await res.json();
            console.log("Delete response:", result);

            if (result.success) {
                deleteStatus.textContent = `✓ ${result.message}`;
                deleteStatus.style.color = "#4caf50";
                deleteStatus.style.display = "block";
                questionIdInput.value = "";
                console.log("Question deleted successfully");
            } else {
                deleteStatus.textContent = `✗ ${result.message}`;
                deleteStatus.style.color = "#f44336";
                deleteStatus.style.display = "block";
                console.error("Delete failed:", result.message);
            }
        } catch (err) {
            deleteStatus.textContent = `✗ Error: ${err.message}`;
            deleteStatus.style.color = "#f44336";
            deleteStatus.style.display = "block";
            console.error("Delete request error:", err);
        } finally {
            deleteQuestionBtn.disabled = false;
        }
    }

    async function deleteVideo() {
        const videoId = videoIdInput.value.trim();
        console.log("Delete video button clicked. Video ID:", videoId);
        
        if (!videoId) {
            deleteVideoStatus.textContent = "⚠ Please enter a Video ID";
            deleteVideoStatus.style.color = "#ff9800";
            deleteVideoStatus.style.display = "block";
            console.warn("No video ID provided");
            return;
        }

        if (videoId.length !== 24) {
            deleteVideoStatus.textContent = "⚠ Video ID must be 24 characters (MongoDB ObjectId)";
            deleteVideoStatus.style.color = "#ff9800";
            deleteVideoStatus.style.display = "block";
            console.warn("Invalid video ID format. Length:", videoId.length);
            return;
        }

        deleteVideoBtn.disabled = true;
        deleteVideoStatus.textContent = "Deleting...";
        deleteVideoStatus.style.color = "#2196f3";
        deleteVideoStatus.style.display = "block";

        try {
            console.log("Sending delete request to /delete_video");
            const res = await fetch("/delete_video", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ video_id: videoId }),
            });

            console.log("Response status:", res.status);
            const result = await res.json();
            console.log("Delete response:", result);

            if (result.success) {
                deleteVideoStatus.textContent = `✓ ${result.message}`;
                deleteVideoStatus.style.color = "#4caf50";
                deleteVideoStatus.style.display = "block";
                videoIdInput.value = "";
                console.log("Video deleted successfully");
            } else {
                deleteVideoStatus.textContent = `✗ ${result.message}`;
                deleteVideoStatus.style.color = "#f44336";
                deleteVideoStatus.style.display = "block";
                console.error("Delete failed:", result.message);
            }
        } catch (err) {
            deleteVideoStatus.textContent = `✗ Error: ${err.message}`;
            deleteVideoStatus.style.color = "#f44336";
            deleteVideoStatus.style.display = "block";
            console.error("Delete request error:", err);
        } finally {
            deleteVideoBtn.disabled = false;
        }
    }

    deleteQuestionBtn.addEventListener("click", deleteQuestion);
    deleteVideoBtn.addEventListener("click", deleteVideo);
})();
