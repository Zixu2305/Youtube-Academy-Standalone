from fastapi import FastAPI

from api.routes.recommend import router as recommend_router
from api.routes.search_skills import router as search_skills_router
from api.routes.search_videos import router as search_videos_router


app = FastAPI(title="SkillsFuture & YouTube Vector Search API")
app.include_router(search_skills_router, prefix="/api", tags=["search-skills"])
app.include_router(search_videos_router, prefix="/api", tags=["search-videos"])
app.include_router(recommend_router, prefix="/api", tags=["recommend"])
