from rest_framework import permissions

class IsAdminOrReadOnlyCustom(permissions.BasePermission):
    """
    Custom permission to only allow admin users to edit objects.
    Read operations are allowed for any request.
    """

    def has_permission(self, request, view):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the admin user.
        # Assuming request.user exists and has an 'is_staff' attribute
        return request.user and request.user.is_staff

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to admin users.
        return request.user and request.user.is_staff