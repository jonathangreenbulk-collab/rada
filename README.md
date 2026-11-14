# Rada

Panel personal de seguimiento de buques a partir de páginas públicas de posiciones AIS.

- `scripts/` — actualización periódica (GitHub Actions): consultas espaciadas, topes por ejecución y por día,
  revisión diaria de robots.txt y pausa automática de un sitio si responde con un rechazo.
- `web/` — interfaz: panel completo y widget.
- `pc/` — widget de escritorio para Windows (pywebview).

La configuración (`config.enc`) y los datos (rama `datos`) se publican cifrados con AES-256-GCM.
La clave no está en el repositorio.
