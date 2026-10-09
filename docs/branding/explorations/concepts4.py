BODY = 'x="100" y="100" width="824" height="824" rx="185"'
DR, DX, AW, AH = 112, 215, 52, 560

def icon(bg, axis, left, right, tilt=0):
    ly, ry = 512 - tilt, 512 + tilt
    return (f'<rect {BODY} fill="{bg}"/>'
            f'<rect x="{512 - AW / 2}" y="{512 - AH / 2}" width="{AW}" height="{AH}" rx="{AW / 2}" fill="{axis}"/>'
            f'<circle cx="{512 - DX}" cy="{ly}" r="{DR}" fill="{left}"/>'
            f'<circle cx="{512 + DX}" cy="{ry}" r="{DR}" fill="{right}"/>')

# the mirror is always a third colour: never the colour of either dot
concepts = [
    ('F1 · вращение, тёмный', icon('#1B1F24', '#6CCBFF', '#F2F1EE', '#F2F1EE', tilt=50)),
    ('F2 · вращение, светлый', icon('#F4F2EE', '#0A7CC2', '#161616', '#161616', tilt=50)),
    ('F3 · ровно, серебро', icon('#1B1F24', '#8E99A6', '#F2F1EE', '#F2F1EE')),
    ('F4 · ровно, лёд', icon('#F4F2EE', '#7FB8DC', '#161616', '#161616')),
    ('F5 · два нейтральных', icon('#F4F2EE', '#0A7CC2', '#161616', '#A39C91', tilt=50)),
    ('F6 · тона + зеркало', icon('#F4F2EE', '#8FA3B5', '#161616', '#D97757', tilt=50)),
]

W = 1500
parts = []
for i, (name, body) in enumerate(concepts):
    col, row = i % 3, i // 3
    x, y = col * 500, row * 740
    parts.append(f'<text x="{x + 250}" y="{y + 60}" font-family="-apple-system, Helvetica" font-size="32" font-weight="600" text-anchor="middle" fill="#1C1C1E">{name}</text>')
    parts.append(f'<svg x="{x + 60}" y="{y + 80}" width="380" height="380" viewBox="0 0 1024 1024">{body}</svg>')
    for strip, bg in enumerate(['#F2F4F7', '#1E1E22']):
        sy = y + 480 + strip * 125
        parts.append(f'<rect x="{x + 30}" y="{sy}" width="440" height="112" rx="18" fill="{bg}"/>')
        sx = x + 60
        for sz in (80, 48, 32, 16):
            parts.append(f'<svg x="{sx}" y="{sy + 56 - sz / 2}" width="{sz}" height="{sz}" viewBox="0 0 1024 1024">{body}</svg>')
            sx += sz + 40
    open(f'concept4_{i}.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">{body}</svg>')
open('sheet4.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}"><rect width="{W}" height="{W}" fill="#FFFFFF"/>{"".join(parts)}</svg>')
print('ok')
