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

- [x] **Ligar o link "Machine Learning"** do menu lateral (26/09/2026) — antes
      era `href="#"`; agora leva a uma página própria (`/machine-learning/`)
      que administra os modelos do projeto ativo — esse gerenciamento morava
      num modal na Home, movido de lá porque é sobre modelos, não sobre o
      projeto em si. Ver seção E abaixo pro ambiente de treino em si.
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

## E. Machine Learning — ambiente de treino

- [x] **Classificação por assinatura espectral (scikit-learn, treina no
      próprio Pi)** (26/09/2026) — decisão: YOLO faz sentido pra segmentação
      (contorno real da semente), mas pra classificar a condição de um ROI
      já medido, a assinatura espectral (8 valores de reflectância) é mais
      direta que imagem — e o dataset é tabular/pequeno, treina rápido sem
      GPU. `views.ml_classifier_train`: features = `ROIMeasurement.reflectance`
      (só ROIs com as 8 bandas E uma condição definida, ver
      `_roi_classifier_dataset`), label = condição da bandeja (mesmo
      `_condition_of` da página Analysis). Usuário escolhe o algoritmo
      (Random Forest / SVM / Regressão Logística / KNN) e ajusta
      hiperparâmetros dentro de faixas seguras (`ML_ALGORITHMS`,
      `_clamp_params` — nunca confia em valor vindo do navegador). Valida
      com k-fold estratificado (k = min(5, menor classe)) antes de treinar o
      modelo final com todos os dados; salva o modelo (`joblib`) e os
      resultados (acurácia, matriz de confusão, importância por banda) em
      `Project.model_cls`/`model_cls_info`. `ml_classifier_estimate` cronometra
      1 fit de verdade e multiplica por `k+1` pra estimar o tempo total ANTES
      de treinar — tentamos extrapolar a partir de uma amostra pequena
      primeiro, mas o custo do Random Forest é dominado pelo nº de árvores
      (overhead de paralelismo do joblib), não pelo tamanho da amostra, e
      isso inflava a estimativa em ~8x; medir o fit inteiro é mais lento mas
      correto (dataset aqui é pequeno, então ainda é rápido). Isso **substitui**
      o antigo `auto_classify` baseado em YOLO/imagem — se o CLS do projeto
      foi treinado por esse ambiente (`model_cls_info.algorithm` é um dos
      scikit-learn), `auto_classify` agora recusa com uma mensagem clara em
      vez de tentar carregar o arquivo como YOLO e estourar um erro confuso;
      a integração de verdade desse modelo treinado no botão "🤖 IA" do
      Annotate (prever pelo ROI já medido, não pela imagem) ainda não foi
      feita, fica pra uma próxima rodada. Card de treino ganhou passo a passo
      numerado (1. algoritmo, 2. hiperparâmetros, 3. treinar) com descrição
      curta de cada algoritmo/hiperparâmetro — feedback do usuário foi que a
      versão anterior "funcionava mas não dava pra entender o fluxo".
- [x] **Empacotar dataset YOLO (segmentação/detecção)** (26/09/2026) — treinar
      YOLO de verdade precisa de GPU, que o Pi não tem; `views.ml_dataset_export`
      empacota as anotações num `.zip` no formato YOLO padrão (`images/`+
      `labels/` em `train`/`val`, split aleatório, `data.yaml`), pronto pra
      treinar fora. Mora no botão "⬇ Exportar" de **Annotations** (escolher
      Segmentação/Detecção ali agora baixa esse `.zip` de verdade, com
      imagens — antes baixava só um JSON cru de coordenadas, sem imagem
      nenhuma, inútil pra treinar direto); a página Machine Learning só
      mostra quantas amostras estão anotadas e linka pra lá, pra não ter dois
      fluxos fazendo a mesma coisa.
- [x] **`yolo_trainer_gui.py`** (movido pra `spectral_app/yolo_kit/`, agora vai dentro do .zip) (26/09/2026) — script standalone (só
      stdlib + `ultralytics`, não faz parte do Django nem do Pi) com uma GUI
      Tkinter simples: escolhe o `data.yaml` extraído do `.zip`, escolhe a
      tarefa e os hiperparâmetros (época, tamanho de imagem, batch, tamanho
      do modelo), detecta GPU CUDA automaticamente (cai pra CPU se não
      achar), treina em background e mostra o progresso ao vivo (inclusive
      as barras do tqdm, sem os códigos de cor ANSI sujando o log). No final
      abre a pasta do `best.pt` pra subir de volta em Machine Learning.

## F. Parametrização — rotina de captura (drag-and-drop)

- [ ] **Montar rotina de captura combinando LEDs e filtros** — seção nova na
      página Parametrization, com etapas montadas por drag-and-drop. Só libera
      a montagem depois que os filtros estiverem parametrizados (as 6 posições
      com passos definidos).
      - **Reflectance analysis** é uma etapa opcional, sempre na posição 1 da
        rotina, sem filtro: quando selecionada, percorre todos os comprimentos
        de onda da rotina. Ao selecioná-la, desabilita a escolha de filtro da
        linha correspondente.
      - Depois, duas colunas por etapa: **LED** (obrigatória, comprimento de
        onda) e **Filtro** (opcional, posição 1-6). Ao escolher a posição do
        filtro, já mostra o nome dele (vem de `FilterPosition.name`).
      - Botão **+ Adicionar etapa** pra ir acrescentando linhas.
      - Exemplo: etapa 1 = LED 580 nm + filtro na posição 3 ("Passa Alta
        670nm"); etapa 2 = só LED, sem filtro; etc.
      - Reordenável por arrastar e soltar. Persistência por projeto (mesmo
        escopo de `FilterPosition`/`BandParameter`).
      - Ainda não definido: se a rotina alimenta a captura (Data Acquisition)
        automaticamente ou só fica salva como referência.
