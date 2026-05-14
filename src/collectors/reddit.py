import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# Subreddits relevant to collectibles
SUBREDDITS = [
    "PokemonTCG", "pkmntcgcollections", "pokemoncardcollectors",
    "mtgfinance", "magicTCG",
    "yugioh", "YuGiOhMasterDuel",
    "gamecollecting", "retrogaming", "Gameboy",
]

REDDIT_SEARCH_URL = "https://old.reddit.com/search.json"


@dataclass
class RedditPost:
    title: str
    subreddit: str
    score: int
    num_comments: int
    url: str
    created_utc: datetime


async def search_hype(query: str, max_results: int = 15) -> list[RedditPost]:
    """Search Reddit for recent posts about a product. Sorted by hot/relevance."""
    posts = []

    try:
        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "NerdNostalgia/1.0 (contact: nerdnostalgia@bot.com)"},
            follow_redirects=True,
        ) as client:
            r = await client.get(REDDIT_SEARCH_URL, params={
                "q": query,
                "sort": "relevance",
                "t": "month",
                "limit": str(min(max_results, 25)),
                "type": "link",
            })

            if r.status_code != 200:
                logger.warning(f"Reddit search returned {r.status_code}")
                return []

            data = r.json()
            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                posts.append(RedditPost(
                    title=post.get("title", ""),
                    subreddit=post.get("subreddit", ""),
                    score=post.get("score", 0),
                    num_comments=post.get("num_comments", 0),
                    url=f"https://reddit.com{post.get('permalink', '')}",
                    created_utc=datetime.fromtimestamp(post.get("created_utc", 0)),
                ))
    except Exception as e:
        logger.error(f"Reddit search failed: {e}")

    return posts


def calculate_hype_score(posts: list[RedditPost]) -> tuple[int, str]:
    """
    Calculate a hype score (0-100) based on Reddit activity.
    Returns (score, description).
    """
    if not posts:
        return 0, "Nessuna attivita' su Reddit"

    total_score = sum(p.score for p in posts)
    total_comments = sum(p.num_comments for p in posts)
    num_posts = len(posts)

    # High-engagement subreddits boost the score
    relevant_subs = sum(1 for p in posts if p.subreddit.lower() in
                        [s.lower() for s in SUBREDDITS])

    # Calculate hype score
    hype = 0
    hype += min(30, num_posts * 3)  # Up to 30 for number of posts
    hype += min(30, total_score // 50)  # Up to 30 for upvotes
    hype += min(20, total_comments // 20)  # Up to 20 for comments
    hype += min(20, relevant_subs * 5)  # Up to 20 for relevant subreddits

    hype = min(100, hype)

    if hype >= 70:
        desc = "🔥🔥🔥 HYPE ALTISSIMO - Forte interesse della community"
    elif hype >= 50:
        desc = "🔥🔥 HYPE ALTO - Molto discusso online"
    elif hype >= 30:
        desc = "🔥 HYPE MODERATO - Qualche discussione attiva"
    elif hype >= 10:
        desc = "💬 HYPE BASSO - Poche menzioni"
    else:
        desc = "😴 NESSUN HYPE - Prodotto non discusso"

    return hype, desc
