from django.conf import settings
from django.db import models

DEFAULT_FILTER_NAME = "Nenhum filtro relacionado"


class FilterPosition(models.Model):
    """Uma das 6 posicoes de filtro, referenciada em passos a partir do fim
    de curso (posicao 0 = referencia no limit switch). Por projeto -- cada
    projeto tem sua propria parametrizacao (ver Project.is_default pra a
    parametrizacao base usada como ponto de partida pra projetos novos)."""
    project = models.ForeignKey('Project', null=True, blank=True, on_delete=models.CASCADE,
                                related_name='filter_positions')
    index = models.PositiveSmallIntegerField()  # 1..6, unico POR projeto
    name = models.CharField(max_length=100, default=DEFAULT_FILTER_NAME)
    steps = models.IntegerField(default=0, help_text="Passos a partir do fim de curso")

    class Meta:
        ordering = ["index"]
        unique_together = [("project", "index")]

    def __str__(self):
        return f"Filtro {self.index}: {self.name}"


class GeometricCalibration(models.Model):
    """Calibracao espacial mm/pixel, obtida de um QR de tamanho conhecido
    (o tamanho vem codificado no conteudo do proprio QR). Uma por projeto --
    uma nova calibracao SUBSTITUI a anterior (update_or_create em views.py),
    nao acumula linha no banco a cada captura. on_delete=CASCADE: apagar o
    projeto apaga a calibracao junto (ver views.project_delete e o signal
    post_delete em signals.py, que remove o arquivo de imagem do disco)."""
    project = models.OneToOneField('Project', null=True, blank=True, on_delete=models.CASCADE,
                                   related_name='geometric_calibration')
    mm_per_pixel = models.FloatField()
    px_per_mm = models.FloatField(default=0.0)   # pixels por mm (reciproco)
    qr_mm = models.FloatField()          # lado real do QR (mm), lido do conteudo
    qr_px = models.FloatField()          # lado medio do QR em pixels
    qr_content = models.CharField(max_length=200, blank=True)
    deviation = models.FloatField(default=0.0)  # divergencia entre os 4 lados (0=perfeito)
    qr_points = models.JSONField(default=list, blank=True)  # [[x,y], ...] os 4 cantos, em pixels da imagem
    image = models.ImageField(upload_to='geometric_parametrization/', blank=True)  # frame de calibracao
    # auto_now (NAO auto_now_add): como a linha e reaproveitada via
    # update_or_create (update numa linha ja existente, nao um insert novo),
    # auto_now_add so pegaria a data da PRIMEIRA calibracao de sempre e nunca
    # avancaria numa recalibracao -- auto_now atualiza em todo save().
    created = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.mm_per_pixel:.5f} mm/px ({self.created:%Y-%m-%d %H:%M})"


class ReflectanceCalibration(models.Model):
    """Referencia branca (100%) de reflectancia: uma foto por banda (LED_BANDS)
    e a media de intensidade de cada banda dentro da bounding box selecionada
    pelo usuario sobre a imagem de uma banda de referencia. Uma por projeto --
    uma nova calibracao SUBSTITUI a anterior (update_or_create em views.py),
    nao acumula linha no banco a cada captura. on_delete=CASCADE: ver
    comentario equivalente em GeometricCalibration."""
    project = models.OneToOneField('Project', null=True, blank=True, on_delete=models.CASCADE,
                                   related_name='reflectance_calibration')
    means = models.JSONField(default=dict)   # {"365": 12.3, ..., "850": 45.6}
    bbox_x = models.PositiveIntegerField(default=0)
    bbox_y = models.PositiveIntegerField(default=0)
    bbox_w = models.PositiveIntegerField(default=0)
    bbox_h = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to='reflectance_parametrization/', blank=True)  # banda de referencia usada na selecao
    # auto_now (NAO auto_now_add) -- ver comentario em GeometricCalibration.
    # Aqui importa ainda mais: e o timestamp que views._reflectance_stale usa
    # pra saber se essa calibracao ainda vale (tem que ficar DEPOIS da ultima
    # mudanca de camera/LED, ver CameraSettings.updated/BandParameter.updated).
    created = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Reflectância 100% ({self.created:%Y-%m-%d %H:%M})"


class ReflectanceZeroCalibration(models.Model):
    """Referencia escura (0%) de reflectancia: 1 foto POR BANDA, todas com os
    LEDs apagados, cada uma usando o mesmo tempo de exposicao manual
    configurado pra aquela banda (BandParameter.exposure_time_absolute) --
    ruido/corrente de escuro do sensor varia com a exposicao, entao a
    referencia escura precisa ser tirada nas MESMAS condicoes que serao
    usadas na captura de verdade daquela banda, senao a conta de reflectancia
    fica inconsistente. image guarda so a foto da banda REFL_BASE_BAND, pra
    preview -- as 8 ficam em means. Uma por projeto -- uma nova calibracao
    SUBSTITUI a anterior (update_or_create em views.py). on_delete=CASCADE:
    ver comentario equivalente em GeometricCalibration."""
    project = models.OneToOneField('Project', null=True, blank=True, on_delete=models.CASCADE,
                                   related_name='reflectance_zero_calibration')
    means = models.JSONField(default=dict)   # {"365": 12.3, ..., "850": 8.1}
    image = models.ImageField(upload_to='reflectance_parametrization/', blank=True)
    # auto_now (NAO auto_now_add) -- mesmo motivo de ReflectanceCalibration.created.
    created = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Reflectância 0% ({len(self.means)} bandas) ({self.created:%Y-%m-%d %H:%M})"


class Project(models.Model):
    """Agrupa aquisicoes pra fins de anotacao/segmentacao (tela de Analysis).
    Equivalente ao 'projeto' da annotation tool original -- la cada projeto
    apontava pra uma pasta de imagens .hdr no disco; aqui cada projeto
    agrupa DataAcquisition (ver campo project abaixo). Os modelos YOLO (se
    o usuario tiver algum treinado) sao enviados por upload em vez de um
    caminho local, ja que este app roda num servidor acessado pelo navegador
    (o dialogo de arquivo do tkinter do app original nao faz sentido aqui)."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=200)
    is_default = models.BooleanField(default=False)  # projeto usado como base/clone padrao (1 so por vez, POR DONO)
    model_seg = models.FileField(upload_to='models/', blank=True)  # segmentacao
    model_det = models.FileField(upload_to='models/', blank=True)  # deteccao
    model_cls = models.FileField(upload_to='models/', blank=True)  # classificacao (joblib scikit-learn)
    # Metadados do ultimo treino de model_cls -- ver views.ml_classifier_train.
    # {"algorithm":, "params":, "bands":, "classes":, "cv_folds":,
    #  "cv_accuracy_mean":, "cv_accuracy_std":, "confusion_matrix":,
    #  "feature_importance": [{"band":, "importance":}, ...] ou None,
    #  "n_samples":, "trained_at": iso, "train_seconds":}
    model_cls_info = models.JSONField(default=dict, blank=True)
    param_note = models.TextField(blank=True, default="")  # observacao livre da parametrizacao (uma por projeto)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("owner", "name")]

    def __str__(self):
        return self.name


class DataAcquisition(models.Model):
    """Uma aquisicao de dados: 1 foto por banda (LED_BANDS, brilho cheio),
    salva crua e normalizada em disco (ver folder). NAO guarda mais media/
    reflectancia do frame inteiro -- isso misturava todas as sementes da
    bandeja com o fundo e nao representava nada cientifico util; a medida de
    verdade agora e por ROI (ver ROIMeasurement, abaixo). A captura fica em
    memoria ate o usuario confirmar com um nome (ver views.py) -- created e o
    momento do SALVAMENTO, nao da captura. on_delete=CASCADE: apagar o
    projeto apaga a aquisicao (e Annotation/ROIMeasurement ligados a ela)
    junto -- o signal post_delete em signals.py remove a pasta de imagens
    (raw/normalized/rgb) do disco tambem, pra nao sobrar arquivo orfao."""
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE,
                                related_name='acquisitions')
    name = models.CharField(max_length=200, blank=True)
    folder = models.CharField(max_length=200, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Aquisição ({self.created:%Y-%m-%d %H:%M})"


class ROIMeasurement(models.Model):
    """Medicao (por banda + geometria) de UM ROI dentro de uma aquisicao --
    ex.: uma semente, numa bandeja com 25. Cada linha e um ROI; contour ainda
    nao tem formato fechado (json livre) -- a estrutura vai ser definida
    conforme o editor de selecao de ROIs for desenhado. means = media bruta
    (0-255) dentro do ROI, por banda; reflectance = a mesma leitura em %,
    usando a calibracao 100%/0% salva no momento da medicao (vazio se nao
    havia). area_px/bbox_*_px e area_mm2/bbox_*_mm = tamanho do ROI (ver
    comentario nesses campos abaixo)."""
    project = models.ForeignKey('Project', on_delete=models.CASCADE, related_name='roi_measurements')
    acquisition = models.ForeignKey(DataAcquisition, on_delete=models.CASCADE, related_name='roi_measurements')

    class SelectionMethod(models.TextChoices):
        BOX = "box", "Retângulo"
        ELLIPSE = "ellipse", "Elipse"
        POLYGON = "polygon", "Polígono"
        POINT = "point", "Ponto"
        OTSU = "otsu", "Otsu (automático)"
        YOLO_DET = "yolo_det", "YOLO detecção (automático)"
        YOLO_SEG = "yolo_seg", "YOLO segmentação (automático)"

    method = models.CharField(max_length=20, choices=SelectionMethod.choices)
    index = models.PositiveSmallIntegerField(default=0)       # ordem do ROI DENTRO DO METODO (ex.: semente 1..25)
    label = models.CharField(max_length=200, blank=True)      # classe/rotulo opcional desse ROI
    # geometria do ROI, no espaco de pixels da imagem de referencia (ANNOTATION_BAND):
    #  box:              {"shape": "box", "x1":.., "y1":.., "x2":.., "y2":..}
    #  ellipse/polygon:  {"shape": "ellipse"|"polygon", "points": [[x,y], ...]}
    #  point:            {"shape": "point", "x":.., "y":..}
    contour = models.JSONField(default=dict, blank=True)
    means = models.JSONField(default=dict)                    # {"365": 12.3, ..., "850": 45.6}
    reflectance = models.JSONField(default=dict, blank=True)

    # Geometria (area + caixa delimitadora), calculada no momento do
    # roi_measurement_compute -- SEMPRE em pixel (nao depende de calibracao);
    # em mm/mm2 so quando havia GeometricCalibration pra esse projeto naquele
    # momento (null se nao havia -- projeto pode nunca ter calibrado
    # espacial, isso nao bloqueia Data Acquisition, ver _missing_calibrations).
    # area = formula do poligono (shoelace) pra ellipse/polygon, ou
    # largura*altura pra box -- funciona pra QUALQUER forma sem assumir
    # orientacao. bbox_width/height = maior distancia em x e em y entre os
    # pontos do contorno (bounding box ALINHADO AOS EIXOS DA IMAGEM, nao ao
    # eixo natural do objeto) -- por isso "bbox", nao "largura/altura da
    # semente": se o objeto estiver rotacionado em relacao a camera, esses
    # dois valores ficam inflados (decisao consciente do usuario, so serve
    # de referencia aproximada, area e que e a medida confiavel). Nenhum dos
    # dois existe pra ROI tipo "point" (nunca capturou um contorno de verdade).
    area_px = models.FloatField(null=True, blank=True)
    area_mm2 = models.FloatField(null=True, blank=True)
    bbox_width_px = models.FloatField(null=True, blank=True)
    bbox_height_px = models.FloatField(null=True, blank=True)
    bbox_width_mm = models.FloatField(null=True, blank=True)
    bbox_height_mm = models.FloatField(null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["acquisition", "method", "index"]
        # indice por (acquisition, index) sozinho colidiria entre metodos --
        # cada metodo numera seus ROIs desse zero (semente 1 do box e semente
        # 1 da elipse sao linhas diferentes, mesmo mirando a mesma regiao).
        unique_together = [("acquisition", "method", "index")]

    def __str__(self):
        return f"ROI {self.index} ({self.method}) de {self.acquisition}"


class Annotation(models.Model):
    """Anotacao (boxes/poligonos/pontos/label) de uma DataAcquisition,
    desenhada sobre a banda de referencia fixa (ANNOTATION_BAND em views.py).
    Espelha a estrutura do JSON por-imagem da annotation tool original."""
    acquisition = models.OneToOneField(DataAcquisition, on_delete=models.CASCADE,
                                       related_name='annotation')
    boxes = models.JSONField(default=list)          # [[x1,y1,x2,y2], ...]
    labels = models.JSONField(default=list)         # categoria de cada box
    # Elipse desenhada no Annotate -- por baixo do capo ainda e uma lista de
    # pontos (aproximacao poligonal, ver ELLIPSE_PTS/ellipseToPolygon em
    # annotate.html), pra continuar dando pra arrastar um vertice e ajustar
    # o formato depois de desenhar -- mas mora no seu PROPRIO campo, nunca
    # dentro de `polygons`.
    ellipses = models.JSONField(default=list)
    ellipse_labels = models.JSONField(default=list)
    # Poligono desenhado a mao (lasso) ou gerado por auto-segmentacao
    # (Otsu/YOLO seg/boxes->poligono) -- NUNCA elipse, ver `ellipses` acima.
    # Campo separado de proposito: antes elipse virava poligono de 32 pontos
    # e ficava misturada no mesmo array (so um heuristico por contagem de
    # pontos dizia qual era qual depois) -- um poligono desenhado a mao que
    # calhasse de ter exatamente 32 pontos virava "elipse" por engano.
    polygons = models.JSONField(default=list)        # [[[x,y], ...], ...]
    poly_labels = models.JSONField(default=list)
    points = models.JSONField(default=list)          # [[x,y], ...]
    point_labels = models.JSONField(default=list)
    image_label = models.CharField(max_length=200, blank=True)  # classificacao da amostra inteira
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Anotação de {self.acquisition}"


class CameraSettings(models.Model):
    """Controles V4L2/UVC da Arducam OV9281, um registro por projeto (ver
    ArducamCamera.apply_controls em camera_functions.py, que reaplica esses
    valores no hardware de verdade via v4l2-ctl). Nomes e faixas conferidos
    direto na camera com `v4l2-ctl -d /dev/video0 --list-ctrls-menus`."""
    project = models.OneToOneField('Project', on_delete=models.CASCADE, related_name='camera_settings')

    brightness = models.IntegerField(default=0)                  # -64..64
    contrast = models.IntegerField(default=32)                   # 0..64
    saturation = models.IntegerField(default=64)                 # 0..128
    hue = models.IntegerField(default=0)                         # -40..40
    gamma = models.IntegerField(default=100)                     # 72..500
    gain = models.IntegerField(default=0)                        # 0..100
    sharpness = models.IntegerField(default=3)                   # 0..6
    backlight_compensation = models.IntegerField(default=1)      # 0..2
    power_line_frequency = models.IntegerField(default=2)        # 0=desligado, 1=50Hz, 2=60Hz

    # Automatico (WB e exposicao) comeca DESLIGADO por padrao -- ligado, a
    # camera reajusta cor/exposicao sozinha a cada captura, o que varia o
    # brilho entre bandas diferentes e atrapalha a comparacao de
    # reflectancia entre elas. Os valores manuais abaixo (157/4600) sao o
    # ponto de partida -- ja proximos do que o automatico costumava escolher.
    white_balance_automatic = models.BooleanField(default=False)
    white_balance_temperature = models.IntegerField(default=4600)  # 2800..6500 (so com o automatico desligado)

    auto_exposure = models.BooleanField(default=False)           # True = Aperture Priority (automatico)
    exposure_time_absolute = models.IntegerField(default=157)    # 1..5000, unidade 100us (so manual)
    exposure_dynamic_framerate = models.BooleanField(default=False)

    # Quando qualquer controle mudou por ultimo -- usado pra saber se uma
    # calibracao de reflectancia (100%/0%) ficou desatualizada (ver
    # views._reflectance_stale). auto_now so atualiza em .save(), NAO em
    # QuerySet.update() -- os poucos lugares que usam update() direto
    # (param_band_auto_expose) setam esse campo na mao.
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Câmera ({self.project})"


class BandParameter(models.Model):
    """Parametros de captura de cada banda de LED (independente dos filtros),
    por projeto -- ver FilterPosition. intensity: 0-255 (dutycycle do PWM).
    exposure_time_absolute: tempo de exposicao MANUAL dessa banda (unidade
    100us, mesma faixa/unidade de CameraSettings.exposure_time_absolute) --
    bandas com LED/sensor mais fraco ou mais forte raramente cabem numa
    exposicao unica sem uma saturar e outra ficar escura demais. Usados de
    verdade nas capturas (ver LED_BANDS/_led_set em views.py), nao so no
    preview da tela de Parametrizacao."""
    project = models.ForeignKey('Project', null=True, blank=True, on_delete=models.CASCADE,
                                related_name='band_parameters')
    nm = models.CharField(max_length=8)   # "365".."850", unico POR projeto
    order = models.PositiveSmallIntegerField(default=0)
    intensity = models.PositiveSmallIntegerField(default=0)  # 0..255
    exposure_time_absolute = models.IntegerField(default=157)  # 1..5000, unidade 100us

    # Idem CameraSettings.updated -- QuerySet.update() (usado em param_save e
    # param_band_auto_expose) tem que setar isso na mao, auto_now nao pega.
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]
        unique_together = [("project", "nm")]

    def __str__(self):
        return f"{self.nm}nm @ {self.intensity} ({self.exposure_time_absolute*100}us)"
