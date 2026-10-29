def user_role_context(request):
    """
    Context processor para disponibilizar permissões da sessão e papéis nos templates.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "is_admin": False,
            "is_analista": False,
            "can_export": False,
            "can_reveal_phone": False,
            "user_role": None,
        }

    user = request.user
    return {
        "is_admin": user.is_admin,
        "is_analista": user.is_analista,
        "can_export": user.can_export,
        "can_reveal_phone": user.can_reveal_phone,
        "user_role": user.role,
    }
