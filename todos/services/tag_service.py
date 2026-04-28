import logging
from django.db import IntegrityError
from todos.models import Tag

logger = logging.getLogger("keep_up")


class TagService:
    """Service for managing user-defined tags (local only, not synced)."""

    @staticmethod
    def create_tag(owner_id: str, name: str, color: str = "#6B7280") -> Tag:
        """
        Create a new tag for a user.

        Args:
            owner_id: UUID of the tag owner
            name: Tag name (max 64 chars, must be unique per user)
            color: Hex color code (default: gray)

        Returns:
            Created Tag instance

        Raises:
            ValueError: If name is empty or already exists for this user
        """
        name = name.strip() if name else ""
        if not name:
            raise ValueError("Tag name cannot be empty.")

        try:
            tag = Tag.objects.create(owner_id=owner_id, name=name, color=color)
            logger.info(f"Created tag '{name}' for user {owner_id}")
            return tag
        except IntegrityError as e:
            logger.warning(f"Tag '{name}' already exists for user {owner_id}: {str(e)}")
            raise ValueError(
                f"A tag named '{name}' already exists. Please use a different name."
            )

    @staticmethod
    def update_tag(owner_id: str, tag_id: str, **updates) -> Tag:
        """
        Update a tag.

        Args:
            owner_id: UUID of the tag owner (for authorization)
            tag_id: UUID of the tag to update
            **updates: Fields to update (name, color)

        Returns:
            Updated Tag instance

        Raises:
            Tag.DoesNotExist: If tag not found or doesn't belong to user
            ValueError: If new name already exists for this user
        """
        tag = Tag.objects.get(id=tag_id, owner_id=owner_id)

        if "name" in updates:
            new_name = updates["name"].strip() if updates["name"] else ""
            if not new_name:
                raise ValueError("Tag name cannot be empty.")

            # Check if new name already exists (and it's not the same tag)
            if (
                Tag.objects.filter(owner_id=owner_id, name=new_name)
                .exclude(id=tag_id)
                .exists()
            ):
                raise ValueError(
                    f"A tag named '{new_name}' already exists. Please use a different name."
                )
            tag.name = new_name

        if "color" in updates:
            tag.color = updates["color"]

        tag.save()
        logger.info(f"Updated tag {tag_id} for user {owner_id}")
        return tag

    @staticmethod
    def delete_tag(owner_id: str, tag_id: str) -> None:
        """
        Delete a tag (hard delete since tags are local-only).

        Args:
            owner_id: UUID of the tag owner (for authorization)
            tag_id: UUID of the tag to delete

        Raises:
            Tag.DoesNotExist: If tag not found or doesn't belong to user
        """
        tag = Tag.objects.get(id=tag_id, owner_id=owner_id)
        tag.delete()
        logger.info(f"Deleted tag {tag_id} for user {owner_id}")

    @staticmethod
    def get_user_tags(owner_id: str):
        """Get all tags for a user, ordered by name."""
        return Tag.objects.filter(owner_id=owner_id).order_by("name")

    @staticmethod
    def get_tag(owner_id: str, tag_id: str) -> Tag:
        """Retrieve a single tag by ID (with ownership check)."""
        return Tag.objects.get(id=tag_id, owner_id=owner_id)
