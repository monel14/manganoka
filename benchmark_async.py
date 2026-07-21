#!/usr/bin/env python3
"""
Benchmark pour démontrer les gains de performance avec httpx.AsyncClient
"""
import asyncio
import time
from scraper.client import get_html, close_http_client


async def fetch_multiple_pages_async(num_pages=5):
    """Fetch plusieurs pages en parallèle (asynchrone)"""
    start = time.time()
    
    tasks = []
    for i in range(1, num_pages + 1):
        path = f"/lastupdates.php?list={i}"
        tasks.append(get_html(path))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    duration = time.time() - start
    
    success_count = sum(1 for r in results if isinstance(r, str))
    
    return {
        "method": "async (httpx.AsyncClient)",
        "pages": num_pages,
        "success": success_count,
        "duration": duration,
        "avg_per_page": duration / num_pages if num_pages > 0 else 0,
    }


async def fetch_multiple_pages_sequential(num_pages=5):
    """Fetch plusieurs pages séquentiellement (pour comparaison)"""
    start = time.time()
    
    results = []
    for i in range(1, num_pages + 1):
        path = f"/lastupdates.php?list={i}"
        try:
            html = await get_html(path)
            results.append(html)
        except Exception as e:
            results.append(e)
    
    duration = time.time() - start
    
    success_count = sum(1 for r in results if isinstance(r, str))
    
    return {
        "method": "sequential",
        "pages": num_pages,
        "success": success_count,
        "duration": duration,
        "avg_per_page": duration / num_pages if num_pages > 0 else 0,
    }


async def main():
    print("=" * 60)
    print("BENCHMARK: httpx.AsyncClient vs Sequential")
    print("=" * 60)
    print()
    
    # Test séquentiel
    print("⏳ Test séquentiel (5 pages)...")
    result_seq = await fetch_multiple_pages_sequential(5)
    print(f"✅ Méthode: {result_seq['method']}")
    print(f"   Pages récupérées: {result_seq['success']}/{result_seq['pages']}")
    print(f"   Durée totale: {result_seq['duration']:.2f}s")
    print(f"   Moyenne par page: {result_seq['avg_per_page']:.2f}s")
    print()
    
    # Test parallèle
    print("⚡ Test parallèle (5 pages)...")
    result_async = await fetch_multiple_pages_async(5)
    print(f"✅ Méthode: {result_async['method']}")
    print(f"   Pages récupérées: {result_async['success']}/{result_async['pages']}")
    print(f"   Durée totale: {result_async['duration']:.2f}s")
    print(f"   Moyenne par page: {result_async['avg_per_page']:.2f}s")
    print()
    
    # Calcul du gain
    if result_seq['duration'] > 0:
        speedup = result_seq['duration'] / result_async['duration']
        print("=" * 60)
        print(f"🚀 GAIN DE PERFORMANCE: {speedup:.2f}x plus rapide")
        print(f"   Temps économisé: {result_seq['duration'] - result_async['duration']:.2f}s")
        print("=" * 60)
    
    # Cleanup
    await close_http_client()


if __name__ == "__main__":
    asyncio.run(main())
