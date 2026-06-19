from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes.learner_portal import api_router as learner_api_router
from api.routes.learner_portal import page_router as learner_page_router
from api.routes.multi_label import router as multi_label_router
from api.routes.quiz import router as quiz_router
from api.routes.recommend import router as recommend_router
from api.routes.search_skills import router as search_skills_router
from api.routes.search_videos import router as search_videos_router


app = FastAPI(title="SkillsFuture Vector Search API")
app.include_router(search_skills_router, prefix="/api", tags=["search"])
app.include_router(search_videos_router, prefix="/api", tags=["search-videos"])
app.include_router(recommend_router, prefix="/api", tags=["recommend"])
app.include_router(quiz_router, prefix="/api", tags=["quiz"])
app.include_router(learner_api_router, prefix="/api", tags=["academy"])
app.include_router(multi_label_router, prefix="/api", tags=["multi-label"])
app.include_router(learner_page_router)
app.mount(
    "/academy/static",
    StaticFiles(directory=str((Path(__file__).resolve().parent / "frontend" / "academy" / "static"))),
    name="academy-static",
)
