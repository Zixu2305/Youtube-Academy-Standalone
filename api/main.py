from fastapi import FastAPI

from api.routes.search_skills import router as search_skills_router


app = FastAPI(title="SkillsFuture Vector Search API")
app.include_router(search_skills_router, prefix="/api", tags=["search"])
