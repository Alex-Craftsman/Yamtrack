from django.urls import path

from . import views

urlpatterns = [
    path("calendar/", views.CalendarView.as_view(), name="api_calendar"),
    path(
        "calendar/update/",
        views.UpdateCalendarView.as_view(),
        name="api_update_calendar",
    ),
    path("lists/", views.ListsView.as_view(), name="api_lists"),
    path("lists/<int:id>/", views.ListDetailView.as_view(), name="api_list_detail"),
    path(
        "lists/<int:id>/items/",
        views.ListAddItemView.as_view(),
        name="api_list_add_item",
    ),
    path(
        "lists/<int:id>/items/<int:item_id>/",
        views.ListRemoveItemView.as_view(),
        name="api_list_remove_item",
    ),
    path("media/", views.MediaListView.as_view(), name="api_media_list"),
    path(
        "media/<str:media_type>/",
        views.MediaTypeListView.as_view(),
        name="api_media_type_list",
    ),
    path(
        "media/<str:media_type>/<str:source>/<int:id>/",
        views.MediaDetailView.as_view(),
        name="api_media_detail",
    ),
    path(
        "media/<str:media_type>/<str:source>/<int:id>/history/",
        views.MediaHistoryView.as_view(),
        name="api_media_history",
    ),
    path(
        "media/<str:media_type>/<str:source>/<int:id>/sync/",
        views.MediaSyncView.as_view(),
        name="api_media_sync",
    ),
    path(
        "media/<str:media_type>/<str:source>/<int:id>/lists/",
        views.MediaAddToListView.as_view(),
        name="api_media_add_to_list",
    ),
    path(
        "search/<str:media_type>/",
        views.SearchProviderView.as_view(),
        name="api_search_provider",
    ),
    path("statistics/", views.StatisticsView.as_view(), name="api_statistics"),
]
