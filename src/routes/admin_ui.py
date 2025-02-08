from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from pathlib import Path

router = APIRouter(prefix="/admin-ui")

# Set up templates
templates = Jinja2Templates(directory="templates")

@router.get("/")
async def admin_panel(request: Request):
    """Serve the admin panel UI"""
    return templates.TemplateResponse(
        "admin.html", 
        {"request": request}
    ) 