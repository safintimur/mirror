# Логотип Mirror

Актуальный исходник: [`app/Resources/Mirror.icon`](../../app/Resources/Mirror.icon). Его компилирует `app/build.sh`; старые концепты не участвуют в сборке.

Принята форма F1/F2: две одинаковые точки со сдвигом по диагонали, отдельная ось-зеркало третьего цвета. Без орбиты и разделённого фона. На слоях включён нативный Liquid Glass. Светлый вариант — чёрные точки и синяя ось; тёмный — белые точки и ледяная ось.

## История выбора

В `explorations` сохранены четыре раунда от 08.10.2026 и их Python-генераторы:

| Лист | Варианты | Результат |
| --- | --- | --- |
| [sheet.svg.png](explorations/sheet.svg.png) | A–C, объём и орбита | Отклонены |
| [sheet2.svg.png](explorations/sheet2.svg.png) | A2, D1–D2, отсылки к провайдерам | Отклонены |
| [sheet3.svg.png](explorations/sheet3.svg.png) | E1–E6, плоский знак | Сплит отклонён, диагональный сдвиг сохранён |
| [sheet4.svg.png](explorations/sheet4.svg.png) | F1–F6, отдельный цвет зеркала | Выбраны F1/F2, затем добавлен Liquid Glass |

Генераторы пишут SVG в текущую папку. Запускать из временной папки, например из корневого `build/`, а не рядом с исходниками.

## Превью

В `previews` — снимки окна «О программе», установщика и светлой иконки в разных размерах от 08.10.2026. Это исторические снимки; тёмный фон на листе размеров не означает проверку тёмного варианта иконки.

## Фон DMG

Исходник — `app/Resources/dmg/background.svg`, готовый TIFF содержит масштабы 1× и 2×. Сохранённый инструмент `app/tools/svg2png.swift` использует рендерер AppKit. Из корня репозитория:

```sh
mkdir -p build/branding
swiftc -O app/tools/svg2png.swift -o build/branding/svg2png
build/branding/svg2png app/Resources/dmg/background.svg build/branding/bg1.png 660 480
build/branding/svg2png app/Resources/dmg/background.svg build/branding/bg2.png 1320 960
sips -s dpiWidth 144 -s dpiHeight 144 build/branding/bg2.png
tiffutil -cathidpicheck build/branding/bg1.png build/branding/bg2.png -out build/branding/background.tiff
```

После визуальной проверки готовый TIFF можно перенести в `app/Resources/dmg/background.tiff`.
