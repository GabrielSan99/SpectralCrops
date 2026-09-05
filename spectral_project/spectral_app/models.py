from django.db import models

DEFAULT_FILTER_NAME = "Nenhum filtro relacionado"


class FilterPosition(models.Model):
    """Uma das 6 posicoes de filtro, referenciada em passos a partir do fim
    de curso (posicao 0 = referencia no limit switch)."""
    index = models.PositiveSmallIntegerField(unique=True)  # 1..6
    name = models.CharField(max_length=100, default=DEFAULT_FILTER_NAME)
    steps = models.IntegerField(default=0, help_text="Passos a partir do fim de curso")

    class Meta:
        ordering = ["index"]

    def __str__(self):
        return f"Filtro {self.index}: {self.name}"


class GeometricCalibration(models.Model):
    """Calibracao espacial mm/pixel, obtida de um QR de tamanho conhecido
    (o tamanho vem codificado no conteudo do proprio QR)."""
    mm_per_pixel = models.FloatField()
    px_per_mm = models.FloatField(default=0.0)   # pixels por mm (reciproco)
    qr_mm = models.FloatField()          # lado real do QR (mm), lido do conteudo
    qr_px = models.FloatField()          # lado medio do QR em pixels
    qr_content = models.CharField(max_length=200, blank=True)
    deviation = models.FloatField(default=0.0)  # divergencia entre os 4 lados (0=perfeito)
    image = models.ImageField(upload_to='geometric/', blank=True)  # frame de calibracao
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.mm_per_pixel:.5f} mm/px ({self.created:%Y-%m-%d %H:%M})"


class ReflectanceCalibration(models.Model):
    """Referencia branca (100%) de reflectancia: uma foto por banda (LED_BANDS)
    e a media de intensidade de cada banda dentro da bounding box selecionada
    pelo usuario sobre a imagem de uma banda de referencia."""
    means = models.JSONField(default=dict)   # {"365": 12.3, ..., "850": 45.6}
    bbox_x = models.PositiveIntegerField(default=0)
    bbox_y = models.PositiveIntegerField(default=0)
    bbox_w = models.PositiveIntegerField(default=0)
    bbox_h = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to='reflectance/', blank=True)  # banda de referencia usada na selecao
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"Reflectância 100% ({self.created:%Y-%m-%d %H:%M})"


class BandParameter(models.Model):
    """Intensidade parametrizada de cada banda de LED (independente dos filtros).
    intensity em 0-255 (dutycycle do PWM)."""
    nm = models.CharField(max_length=8, unique=True)   # "365".."850"
    order = models.PositiveSmallIntegerField(default=0)
    intensity = models.PositiveSmallIntegerField(default=0)  # 0..255

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.nm}nm @ {self.intensity}"
