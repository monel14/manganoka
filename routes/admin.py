"""Routes d'administration pour monitorer le système d'indexation."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/admin/indexing-stats")
async def indexing_stats():
    """
    Affiche les statistiques d'utilisation du quota Google Indexing.
    
    Retourne:
    - Quota utilisé aujourd'hui
    - Quota restant
    - Stats des 7 derniers jours
    - Top 10 des URLs récemment indexées
    """
    db_path = Path(__file__).resolve().parent.parent / "cache.db"
    
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            # Stats du jour
            today = date.today().isoformat()
            today_row = conn.execute(
                "SELECT requests_sent, requests_blocked FROM google_indexing_stats WHERE date = ?",
                (today,)
            ).fetchone()
            
            if today_row:
                today_sent, today_blocked = today_row
            else:
                today_sent, today_blocked = 0, 0
            
            # Stats des 7 derniers jours
            seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
            week_stats = conn.execute(
                "SELECT date, requests_sent, requests_blocked FROM google_indexing_stats "
                "WHERE date >= ? ORDER BY date DESC",
                (seven_days_ago,)
            ).fetchall()
            
            # Top 10 URLs récemment indexées
            recent_urls = conn.execute(
                "SELECT url, datetime(pinged_at, 'unixepoch') as pinged "
                "FROM google_indexed_urls ORDER BY pinged_at DESC LIMIT 10"
            ).fetchall()
            
            # Total URLs indexées
            total_urls = conn.execute(
                "SELECT COUNT(*) FROM google_indexed_urls"
            ).fetchone()[0]
            
            # URLs indexées aujourd'hui (dernières 24h)
            import time
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
                    "percentage_used": round((today_sent / 200) * 100, 1)
                },
                "last_7_days": [
                    {
                        "date": row[0],
                        "sent": row[1],
                        "blocked": row[2],
                        "total_attempts": row[1] + row[2]
                    }
                    for row in week_stats
                ],
                "urls": {
                    "total_indexed": total_urls,
                    "indexed_today": urls_today,
                    "recent": [
                        {"url": url, "pinged_at": pinged}
                        for url, pinged in recent_urls
                    ]
                },
                "recommendations": _get_recommendations(today_sent, today_blocked, urls_today)
            })
    except Exception as e:
        return JSONResponse(
            {"error": str(e), "message": "Impossible de récupérer les stats"},
            status_code=500
        )


@router.get("/admin/webhook-stats")
async def webhook_stats():
    """
    Affiche les statistiques d'utilisation du webhook Pinterest/Make.com.
    
    Retourne:
    - Nombre de pins envoyés aujourd'hui
    - Quota restant
    - Stats des 7 derniers jours
    - Derniers pins envoyés
    """
    db_path = Path(__file__).resolve().parent.parent / "cache.db"
    
    try:
        with sqlite3.connect(str(db_path), timeout=10.0) as conn:
            # Stats du jour
            today = date.today().isoformat()
            today_row = conn.execute(
                "SELECT count FROM webhook_daily_quota WHERE date = ?",
                (today,)
            ).fetchone()
            
            pins_today = today_row[0] if today_row else 0
            
            # Stats des 7 derniers jours
            seven_days_ago = (date.today() - timedelta(days=7)).isoformat()
            week_stats = conn.execute(
                "SELECT date, count FROM webhook_daily_quota "
                "WHERE date >= ? ORDER BY date DESC",
                (seven_days_ago,)
            ).fetchall()
            
            # Derniers pins envoyés
            recent_pins = conn.execute(
                "SELECT guid, datetime(posted_at, 'unixepoch') as posted "
                "FROM posted_pins ORDER BY posted_at DESC LIMIT 10"
            ).fetchall()
            
            # Total pins envoyés
            total_pins = conn.execute(
                "SELECT COUNT(*) FROM posted_pins"
            ).fetchone()[0]
            
            return JSONResponse({
                "quota": {
                    "daily_limit": 50,
                    "used_today": pins_today,
                    "remaining_today": max(0, 50 - pins_today),
                    "percentage_used": round((pins_today / 50) * 100, 1)
                },
                "last_7_days": [
                    {"date": row[0], "pins_sent": row[1]}
                    for row in week_stats
                ],
                "pins": {
                    "total_sent": total_pins,
                    "sent_today": pins_today,
                    "recent": [
                        {"guid": guid, "posted_at": posted}
                        for guid, posted in recent_pins
                    ]
                },
                "recommendations": _get_webhook_recommendations(pins_today, total_pins)
            })
    except Exception as e:
        return JSONResponse(
            {"error": str(e), "message": "Impossible de récupérer les stats webhook"},
            status_code=500
        )


def _get_recommendations(sent: int, blocked: int, urls_today: int) -> list[str]:
    """Génère des recommandations basées sur l'utilisation."""
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


def _get_webhook_recommendations(pins_today: int, total_pins: int) -> list[str]:
    """Génère des recommandations pour le webhook Pinterest."""
    recs = []
    
    if pins_today >= 50:
        recs.append("🔴 QUOTA QUOTIDIEN ATTEINT (50 pins max/jour). Aucun nouveau pin ne sera publié aujourd'hui.")
    elif pins_today >= 40:
        recs.append("⚠️ ATTENTION: Proche de la limite quotidienne (50 pins/jour). Reste: " + str(50 - pins_today))
    elif pins_today >= 30:
        recs.append("💡 Utilisation élevée aujourd'hui. Surveillez pour éviter le spam Pinterest.")
    elif pins_today > 0:
        recs.append("✅ Utilisation normale. " + str(50 - pins_today) + " pins restants aujourd'hui.")
    else:
        recs.append("✅ Aucun pin envoyé aujourd'hui.")
    
    if total_pins > 1000:
        recs.append("📊 Plus de 1000 pins publiés au total. Excellent travail SEO!")
    
    return recs
