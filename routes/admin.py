"""Routes d'administration pour monitorer le système d'indexation."""
from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/admin/indexing-stats")
async def indexing_stats():
    """Affiche les statistiques d'utilisation du quota Google Indexing."""
    db_path = Path(__file__).resolve().parent.parent / "cache.db"

    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            today = date.today().isoformat()
            today_row = conn.execute(
                "SELECT requests_sent, requests_blocked FROM google_indexing_stats WHERE date = ?",
                (today,)
            ).fetchone()
            today_sent, today_blocked = today_row if today_row else (0, 0)

            seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
            week_stats = conn.execute(
                "SELECT date, requests_sent, requests_blocked FROM google_indexing_stats "
                "WHERE date >= ? ORDER BY date DESC",
                (seven_days_ago,)
            ).fetchall()

            recent_urls = conn.execute(
                "SELECT url, datetime(pinged_at, 'unixepoch') as pinged "
                "FROM google_indexed_urls ORDER BY pinged_at DESC LIMIT 10"
            ).fetchall()

            total_urls = conn.execute(
                "SELECT COUNT(*) FROM google_indexed_urls"
            ).fetchone()[0]

            yesterday = time.time() - 86400
            urls_today = conn.execute(
                "SELECT COUNT(*) FROM google_indexed_urls WHERE pinged_at > ?",
                (yesterday,)
            ).fetchone()[0]

            return JSONResponse({
                "quota": {
                    "daily_limit": 200,
                    "used_today": today_sent,
                    "remaining_today": max(0, 200 - today_sent),
                    "blocked_today": today_blocked,
                    "percentage_used": round((today_sent / 200) * 100, 1),
                },
                "last_7_days": [
                    {"date": row[0], "sent": row[1], "blocked": row[2], "total_attempts": row[1] + row[2]}
                    for row in week_stats
                ],
                "urls": {
                    "total_indexed": total_urls,
                    "indexed_today": urls_today,
                    "recent": [{"url": url, "pinged_at": pinged} for url, pinged in recent_urls],
                },
                "recommendations": _get_recommendations(today_sent, today_blocked, urls_today),
            })
    except Exception as e:
        return JSONResponse(
            {"error": str(e), "message": "Impossible de récupérer les stats"},
            status_code=500,
        )


def _get_recommendations(sent: int, blocked: int, urls_today: int) -> list[str]:
    recs = []
    if sent > 150:
        recs.append("⚠️ ATTENTION: Quota presque épuisé (>75%). Risque d'atteindre la limite.")
    if sent > 200:
        recs.append("🔴 QUOTA DÉPASSÉ! Les nouvelles URLs ne seront pas indexées aujourd'hui.")
    if blocked == 0 and urls_today > 50:
        recs.append("💡 Beaucoup de nouvelles URLs. Le système de déduplication fonctionne bien.")
    if blocked > sent * 2:
        recs.append("✅ Excellent! Le cache bloque la majorité des doublons.")
    if sent < 50:
        recs.append("✅ Utilisation normale du quota. Marge confortable disponible.")
    if not recs:
        recs.append("✅ Tout fonctionne normalement.")
    return recs
