from django.conf import settings
from django.db import models, transaction
from django.db.models import F, Max, Prefetch, Q

from app.models import Item


class CustomListManager(models.Manager):
    """Manager for custom lists."""

    def get_user_lists(self, user):
        """Return the custom lists that the user owns or collaborates on."""
        return (
            self.filter(Q(owner=user) | Q(collaborators=user))
            .select_related("owner")
            .prefetch_related(
                "collaborators",
                Prefetch(
                    "items",
                    queryset=Item.objects.order_by("-customlistitem__date_added"),
                ),
                Prefetch(
                    "customlistitem_set",
                    queryset=CustomListItem.objects.order_by("-date_added"),
                ),
            )
            .distinct()
        )

    def get_user_lists_with_item(self, user, item):
        """Return user lists with item membership status."""
        return (
            self.filter(Q(owner=user) | Q(collaborators=user))
            .annotate(
                has_item=models.Exists(
                    CustomListItem.objects.filter(
                        custom_list_id=models.OuterRef("id"),
                        item=item,
                    ),
                ),
            )
            .prefetch_related("collaborators")
            .distinct()
            .order_by("name")
        )


class CustomList(models.Model):
    """Model for custom lists."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="collaborated_lists",
        blank=True,
    )
    items = models.ManyToManyField(
        Item,
        related_name="custom_lists",
        blank=True,
        through="CustomListItem",
    )

    objects = CustomListManager()

    class Meta:
        """Meta options for the model."""

        ordering = ["name"]

    def __str__(self):
        """Return the name of the custom list."""
        return self.name

    def user_can_view(self, user):
        """Check if the user can view the list."""
        return self.owner == user or user in self.collaborators.all()

    def user_can_edit(self, user):
        """Check if the user can edit the list."""
        return self.owner == user or user in self.collaborators.all()

    def user_can_delete(self, user):
        """Check if the user can delete the list."""
        return self.owner == user

    def get_list_item(self, list_item_id, *, include_item=False):
        """Return a list item by list-scoped id for this custom list."""
        queryset = CustomListItem.objects.filter(custom_list=self)
        if include_item:
            queryset = queryset.select_related("item")
        return queryset.get(list_item_id=list_item_id)

    @property
    def image(self):
        """Return the image of the first item in the list."""
        return self.items.first().image if self.items.first() else settings.IMG_NONE


class CustomListItemManager(models.Manager):
    """Manager for custom list items."""

    def get_next_list_item_id(self, custom_list_id):
        """Return the next sequential id for an item within a custom list."""
        current_max = (
            self.filter(custom_list_id=custom_list_id)
            .aggregate(max_id=Max("list_item_id"))
            .get("max_id")
        )
        return 0 if current_max is None else current_max + 1

    def bulk_create(self, objs, **kwargs):
        """Assign per-list sequential IDs before creating items in bulk."""
        if not objs:
            return super().bulk_create(objs, **kwargs)

        pending_per_list = {}
        for obj in objs:
            if obj.list_item_id is not None:
                continue

            custom_list_id = obj.custom_list_id
            if custom_list_id not in pending_per_list:
                pending_per_list[custom_list_id] = []
            pending_per_list[custom_list_id].append(obj)

        if pending_per_list:
            with transaction.atomic():
                for custom_list_id, group in pending_per_list.items():
                    base_id = self.get_next_list_item_id(custom_list_id)
                    for index, obj in enumerate(group):
                        obj.list_item_id = base_id + index

                return super().bulk_create(objs, **kwargs)

        return super().bulk_create(objs, **kwargs)

    def get_last_added_date(self, custom_list):
        """Return the last time an item was added to a specific list."""
        try:
            return self.filter(custom_list=custom_list).latest("date_added").date_added
        except self.model.DoesNotExist:
            return None


class CustomListItem(models.Model):
    """Model for items in custom lists."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    custom_list = models.ForeignKey(CustomList, on_delete=models.CASCADE)
    list_item_id = models.PositiveIntegerField(null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)

    objects = CustomListItemManager()

    class Meta:
        """Meta options for the model."""

        ordering = ["date_added"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "custom_list"],
                name="%(app_label)s_customlistitem_unique_item_list",
            ),
            models.UniqueConstraint(
                fields=["custom_list", "list_item_id"],
                name="%(app_label)s_customlistitem_unique_list_item_id",
            ),
        ]

    def __str__(self):
        """Return the name of the list item."""
        return self.item.title

    def save(self, *args, **kwargs):
        """Save the list item assigning a sequential list-scoped id on create."""
        if self._state.adding and self.list_item_id is None:
            self.list_item_id = CustomListItem.objects.get_next_list_item_id(
                self.custom_list_id,
            )

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Delete item and renumber following list items to close numbering gaps."""
        custom_list_id = self.custom_list_id
        removed_list_item_id = self.list_item_id

        with transaction.atomic():
            result = super().delete(*args, **kwargs)

            if removed_list_item_id is not None:
                CustomListItem.objects.filter(
                    custom_list_id=custom_list_id,
                    list_item_id__gt=removed_list_item_id,
                ).update(list_item_id=F("list_item_id") - 1)

        return result
