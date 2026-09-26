# Checklist — próximos passos do SpectralCrops

Levantado em 26/09/2026 a partir de uma revisão do estado atual do software.
Ordem dentro de cada seção é sugestão de prioridade, não obrigação.

## A. Validade científica dos dados

- [x] **Travar filtros/câmera/iluminação/calibrações após a 1ª aquisição de
      dados salva** (26/09/2026) — resolve a rastreabilidade sem precisar de
      snapshot por medição: um projeto só tem uma calibração pra sempre, então
      não existe mais ambiguidade sobre "qual calibração valia pra essa
      medição". Pra mudar qualquer parâmetro depois, cria um projeto novo
      (agora clona também os Controles da Câmera). Modal avisa antes de
      salvar a 1ª aquisição. Guarda tanto na UI (`<fieldset disabled>`)
      quanto na API (`@require_unlocked`, `views.py::_project_locked`).
- [x] **Converter medição de ROI de pixel para mm** (26/09/2026) — `ROIMeasurement`
      ganhou `area_px`/`area_mm2` (shoelace formula, qualquer forma) e
      `bbox_width/height_px/mm` (caixa delimitadora alinhada aos eixos —
      referência aproximada, rotula como "bbox" não "largura/altura da
      semente" pra não sugerir precisão que não tem). Calculado em
      `roi_measurement_compute`: px sempre, mm só quando o projeto tem
      `GeometricCalibration` (opcional, não bloqueia nada). ROI tipo "point"
      fica sem geometria (nunca capturou contorno de verdade). Tabela de
      Medições por ROI e export CSV mostram mm quando calibrado, px como
      fallback quando não.
- [x] **Comparação por grupo/condição experimental** (26/09/2026) — reformulado:
      não é repetibilidade de instrumento (cada bandeja é capturada uma única
      vez), é comparação entre grupos biológicos (ex.: 4 bandejas de 25
      sementes verdes vs. 4 de 25 sementes normais). Reestruturação de
      navegação: "Analysis" (galeria de amostras pra anotar) virou
      **"Annotations"** (`/annotations/`); a antiga tabela de Medições por ROI
      (só acessível por link, fora do menu) foi promovida a **"Analysis"**
      (`/analysis/`, agora no menu lateral) e ganhou filtro por condição
      (usa o label já existente da bandeja, `Annotation.image_label`) +
      estatística agregada (média ± desvio padrão por banda, bruto e
      reflectância, agrupado por condição).

## B. Pontas soltas

- [ ] **Remover ou ligar o link "Machine Learning"** do menu lateral
      (`href="#"` — não leva a lugar nenhum). A funcionalidade de IA já existe
      de verdade dentro do Annotate (botão "🤖 IA": Otsu, YOLO detecção/
      segmentação, classificação). (`spectral_app/templates/partials/sidebar.html`)
- [ ] **Deletar `img_segmentation`** (view + rota) — endpoint órfão do
      protótipo antigo (câmera Bluefox), não referenciado por nenhuma tela,
      sem `@login_required`, com `@csrf_exempt`, escreve arquivo em disco a
      partir de entrada do cliente. (`spectral_app/views.py`,
      `spectral_project/urls.py`)

## C. Segurança (baixa prioridade, uso local)

- [ ] **Trocar/parametrizar a senha padrão do superusuário** (`Admin` /
      `Spectral123`, hardcoded em `spectral_app/signals.py`) — ok isolado na
      bancada, mas é porta aberta se o Pi algum dia entrar numa rede
      compartilhada.

## D. Qualidade de engenharia

- [ ] **Testes automatizados de verdade** (`manage.py test`) pros pontos mais
      críticos — hoje `spectral_app/tests.py` é só o scaffold vazio do
      Django; toda validação desta sessão foi feita com scripts manuais
      descartáveis. Prioridade: fórmula de reflectância (incl. guardas de
      saturação/faixa mínima), staleness de calibração, exposição por banda.
