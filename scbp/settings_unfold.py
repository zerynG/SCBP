from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _


UNFOLD = {
    "SITE_TITLE": "ЦОП Умная приемная комиссия",
    "SITE_HEADER": "Умная приемная комиссия",
    "SITE_SUBHEADER": "Администрирование",
    "SITE_DROPDOWN": [
        {
            "icon": "Globe",
            "title": _("Сайт разработчика"),
            "link": "https://iqnix.tech",
        },
        # ...
    ],
    #"SIDEBAR": {
        #"show_search": True,
        #"show_all_applications": True,
        #"navigation": [
        #    {
        #        "title": _("Пользователи и роли"),
        #        "separator": True,
        #        "collapsible": False,
        #        "items": [
        #            {
        #                "title": _("Роли"),
        #                "permissions": ["auth.view_user"],
        #                "icon": "Shield_Lock",
        #                "link": reverse_lazy("admin:mainboard_userroleassignment_changelist"),
        #            },
        #            {
        #                "title": _("Доступы"),
        #                "permissions": ["auth.view_group"],
        #                "icon": "Ad_Group",
        #                "link": reverse_lazy("admin:auth_group_changelist"),
        #            },
        #            {
        #                "title": _("Профили"),
        #                "permissions": ["auth.view_user"],
        #                "icon": "Badge",
        #                "link": reverse_lazy("admin:mainboard_profile_changelist"),
        #            },
        #            {
        #                "title": _("Пользователи"),
        #                "permissions": ["auth.view_user"],
        #                "icon": "Account_Circle",
        #                "link": reverse_lazy("admin:auth_user_changelist"),
        #            },
        #        ]
        #    },
            # {
            #     "name": "🏢 Структура системы",
            #     "items": [
            #         "mainboard.Form",
            #         "mainboard.WorkSpace",
            #         "mainboard.SubDepartment",
            #         "mainboard.AdditionalInfo",
            #     ],
            # },
            # {
            #     "name": "📚 Образовательные программы",
            #     "items": [
            #         "mainboard.EducationalProgram",
            #         "mainboard.ExamResult",
            #     ],
            # },
            # {
            #     "name": "📝 Абитуриенты и заявления",
            #     "items": [
            #         "mainboard.Applicant",
            #         "mainboard.Application",
            #         "mainboard.Parent",
            #         "mainboard.LegalEntity",
            #         "mainboard.Contract",
            #     ],
            # },
            # {
            #     "name": "📄 Документы и шаблоны",
            #     "items": [
            #         "mainboard.DocumentTemplate",
            #         "mainboard.DocumentField",
            #         "mainboard.FieldMapping",
            #     ],
            # },
       # ],
   # },
    "COLORS": {
        "base": {
            "50": "249 250 251",
            "100": "243 244 246",
            "200": "229 231 235",
            "300": "209 213 219",
            "400": "156 163 175",
            "500": "107 114 128",
            "600": "75 85 99",
            "700": "55 65 81",
            "800": "31 41 55",
            "900": "17 24 39",
            "950": "3 7 18",
        },
        "primary": {
            "50": "245 245 255",  # Более светлый оттенок синего
            "100": "232 232 255",  # Приближен к  # 5858f6
            "200": "213 213 255",  # Приближен к  # 5858f6
            "300": "180 180 254",  # Приближен к  # 5858f6
            "400": "132 132 252",  # Приближен к  # 5858f6
            "500": "88 88 246",  # 5858f6
            "600": "72 72 234",    # Темнее  # 5858f6
            "700": "56 56 206",    # Еще темнее
            "800": "44 44 168",    # Темно-синий
            "900": "32 32 135",    # Очень темный
            "950": "20 20 100",    # Почти черный с синим оттенком
        },
        "font": {
            "subtle-light": "var(--color-base-500)",  # text-base-500
            "subtle-dark": "var(--color-base-400)",  # text-base-400
            "default-light": "var(--color-base-600)",  # text-base-600
            "default-dark": "var(--color-base-300)",  # text-base-300
            "important-light": "var(--color-base-900)",  # text-base-900
            "important-dark": "var(--color-base-100)",  # text-base-100
        },
    },
}
