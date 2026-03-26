from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    Book,
    Comic,
    Game,
    Item,
    Manga,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class ApiTestCase(TestCase):
    """Shared setup and request helpers for API endpoint tests."""

    def setUp(self):
        """Create auth fixtures and patch noisy side effects."""
        self.user1 = get_user_model().objects.create_user(
            username="api-test-user1",
        )
        self.user2 = get_user_model().objects.create_user(
            username="api-test-user2",
        )
        self.auth_headers = {"HTTP_X_API_KEY": self.user1.token}
        self.auth_headers2 = {"HTTP_X_API_KEY": self.user2.token}
        self.invalid_auth_headers = {"HTTP_X_API_KEY": "invalid-token"}

        # Disable external side effects while creating many media fixtures.
        self._fetch_releases_patcher = patch(
            "app.models.Item.fetch_releases",
            return_value=None,
        )
        self._fetch_releases_patcher.start()
        self.addCleanup(self._fetch_releases_patcher.stop)

        self._metadata_patcher = patch(
            "app.models.providers.services.get_media_metadata",
            return_value={
                "max_progress": None,
                "related": {"seasons": [], "recommendations": []},
                "episodes": [],
            },
        )
        self._metadata_patcher.start()
        self.addCleanup(self._metadata_patcher.stop)

        self.items_by_type = {
            MediaTypes.MOVIE.value: [
                Item.objects.create(
                    media_id="701",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 1",
                    image="https://example.com/movie-1.jpg",
                ),
                Item.objects.create(
                    media_id="702",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 2",
                    image="https://example.com/movie-2.jpg",
                ),
                Item.objects.create(
                    media_id="703",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    title="Movie 3",
                    image="https://example.com/movie-3.jpg",
                ),
            ],
            MediaTypes.TV.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 1",
                    image="https://example.com/tv-1.jpg",
                ),
                Item.objects.create(
                    media_id="1002",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 2",
                    image="https://example.com/tv-2.jpg",
                ),
                Item.objects.create(
                    media_id="1003",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.TV.value,
                    title="TV Show 3",
                    image="https://example.com/tv-3.jpg",
                ),
            ],
            MediaTypes.SEASON.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-1.jpg",
                    season_number=1,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-2.jpg",
                    season_number=2,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.SEASON.value,
                    title="TV Show 1",
                    image="https://example.com/season-3.jpg",
                    season_number=3,
                ),
            ],
            MediaTypes.EPISODE.value: [
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-1.jpg",
                    season_number=1,
                    episode_number=1,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-2.jpg",
                    season_number=1,
                    episode_number=2,
                ),
                Item.objects.create(
                    media_id="1001",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    title="TV Show 1",
                    image="https://example.com/episode-3.jpg",
                    season_number=1,
                    episode_number=3,
                ),
            ],
            MediaTypes.ANIME.value: [
                Item.objects.create(
                    media_id="2001",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 1",
                    image="https://example.com/anime-1.jpg",
                ),
                Item.objects.create(
                    media_id="2002",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 2",
                    image="https://example.com/anime-2.jpg",
                ),
                Item.objects.create(
                    media_id="2003",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.ANIME.value,
                    title="Anime 3",
                    image="https://example.com/anime-3.jpg",
                ),
            ],
            MediaTypes.MANGA.value: [
                Item.objects.create(
                    media_id="3001",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 1",
                    image="https://example.com/manga-1.jpg",
                ),
                Item.objects.create(
                    media_id="3002",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 2",
                    image="https://example.com/manga-2.jpg",
                ),
                Item.objects.create(
                    media_id="3003",
                    source=Sources.MAL.value,
                    media_type=MediaTypes.MANGA.value,
                    title="Manga 3",
                    image="https://example.com/manga-3.jpg",
                ),
            ],
            MediaTypes.GAME.value: [
                Item.objects.create(
                    media_id="4001",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 1",
                    image="https://example.com/game-1.jpg",
                ),
                Item.objects.create(
                    media_id="4002",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 2",
                    image="https://example.com/game-2.jpg",
                ),
                Item.objects.create(
                    media_id="4003",
                    source=Sources.IGDB.value,
                    media_type=MediaTypes.GAME.value,
                    title="Game 3",
                    image="https://example.com/game-3.jpg",
                ),
            ],
            MediaTypes.BOOK.value: [
                Item.objects.create(
                    media_id="5001",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 1",
                    image="https://example.com/book-1.jpg",
                ),
                Item.objects.create(
                    media_id="5002",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 2",
                    image="https://example.com/book-2.jpg",
                ),
                Item.objects.create(
                    media_id="5003",
                    source=Sources.OPENLIBRARY.value,
                    media_type=MediaTypes.BOOK.value,
                    title="Book 3",
                    image="https://example.com/book-3.jpg",
                ),
            ],
            MediaTypes.COMIC.value: [
                Item.objects.create(
                    media_id="6001",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 1",
                    image="https://example.com/comic-1.jpg",
                ),
                Item.objects.create(
                    media_id="6002",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 2",
                    image="https://example.com/comic-2.jpg",
                ),
                Item.objects.create(
                    media_id="6003",
                    source=Sources.COMICVINE.value,
                    media_type=MediaTypes.COMIC.value,
                    title="Comic 3",
                    image="https://example.com/comic-3.jpg",
                ),
            ],
        }

        # Various tracked medias for user1.
        self.tv_medias = [
            TV.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.TV.value]
        ]
        self.movie_medias = [
            Movie.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.MOVIE.value]
        ]
        self.anime_medias = [
            Anime.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.ANIME.value]
        ]
        self.manga_medias = [
            Manga.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.MANGA.value]
        ]
        self.game_medias = [
            Game.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.GAME.value]
        ]
        self.book_medias = [
            Book.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.BOOK.value]
        ]
        self.comic_medias = [
            Comic.objects.create(item=item, user=self.user1)
            for item in self.items_by_type[MediaTypes.COMIC.value]
        ]

        self.season_medias = [
            Season.objects.create(
                item=item,
                user=self.user1,
                related_tv=self.tv_medias[0],
                status=Status.IN_PROGRESS.value,
            )
            for item in self.items_by_type[MediaTypes.SEASON.value]
        ]

        self._build_changes_history_fixtures()

    def _build_changes_history_fixtures(self):
        """Create history rows linked to user1 for changes-history endpoint tests."""
        media_for_history = [
            self.movie_medias[0],
            self.tv_medias[0],
            self.season_medias[0],
            self.anime_medias[0],
            self.manga_medias[0],
            self.game_medias[0],
            self.book_medias[0],
            self.comic_medias[0],
        ]

        for index, media in enumerate(media_for_history, start=1):
            media._history_user = self.user1
            media.notes = f"history-seed-{index}"
            media.save()

        # Add one extra change on movie to ensure at least two snapshots exist.
        movie = self.movie_medias[0]
        movie._history_user = self.user1
        movie.score = 8
        movie.save()

        self.changes_history_entries = {
            "movie": movie.history.filter(history_user=self.user1).first(),
            "tv": self.tv_medias[0].history.filter(history_user=self.user1).first(),
            "season": self.season_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "anime": self.anime_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "manga": self.manga_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
            "game": self.game_medias[0].history.filter(history_user=self.user1).first(),
            "book": self.book_medias[0].history.filter(history_user=self.user1).first(),
            "comic": self.comic_medias[0]
            .history.filter(history_user=self.user1)
            .first(),
        }

    def call_api(self, method, url_name, args=(), payload=None, headers=None):
        """Call an API endpoint using a named URL and optional payload."""
        request_method = getattr(self.client, method.lower())
        request_kwargs = dict(headers or {})

        if payload is not None:
            request_kwargs["data"] = payload
            request_kwargs["content_type"] = "application/json"

        return request_method(reverse(url_name, args=args), **request_kwargs)
