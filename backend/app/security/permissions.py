class PermissionManager:
    def __init__(self, settings):
        self.dangerous = set(settings["security"].get("require_confirmation_for", []))

    def requires_confirmation(self, action):
        return action in self.dangerous

    def require(self, action, approved=False):
        if self.requires_confirmation(action) and not approved:
            raise PermissionError(f"Confirmation required for: {action}")
