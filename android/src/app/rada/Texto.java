package app.rada;

import android.graphics.Typeface;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.StyleSpan;

import org.json.JSONArray;
import org.json.JSONObject;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Textos del widget: las mismas reglas que web/rada.js, para que PC, web y teléfono digan lo mismo. */
final class Texto {
    private static final String[] ROMANOS = {"", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"};
    private static final Locale ES = Locale.forLanguageTag("es");
    private static final Pattern DIGITO_FINAL = Pattern.compile(" (\\d)$");

    private final JSONObject d, puertos;
    private final DateTimeFormatter fFecha, fHora;
    final String flota;

    Texto(JSONObject d) {
        this.d = d;
        JSONObject p = d.optJSONObject("puertos");
        puertos = p != null ? p : new JSONObject();
        ZoneId z;
        try {
            String zona = s(d, "zona_horaria");
            z = zona.isEmpty() ? ZoneOffset.UTC : ZoneId.of(zona);
        } catch (Exception e) {
            z = ZoneOffset.UTC;
        }
        fFecha = DateTimeFormatter.ofPattern("d MMM, HH:mm", ES).withZone(z);
        fHora = DateTimeFormatter.ofPattern("HH:mm", ES).withZone(z);
        flota = s(d, "flota");
    }

    /* ------------------------------------------------------------ lectura segura del JSON */
    static String s(JSONObject o, String k) {
        return o == null || o.isNull(k) ? "" : o.optString(k, "");
    }

    static boolean si(JSONObject o, String k) {
        Object v = o.opt(k);
        if (v instanceof Boolean) return (Boolean) v;
        if (v instanceof Number) return ((Number) v).doubleValue() != 0;
        return "true".equals(v);
    }

    static int largo(JSONObject p, String k) {
        JSONArray a = p.optJSONArray(k);
        return a == null ? 0 : a.length();
    }

    private static List<JSONObject> lista(JSONObject p, String k) {
        List<JSONObject> out = new ArrayList<>();
        JSONArray a = p.optJSONArray(k);
        if (a != null) for (int i = 0; i < a.length(); i++) {
            JSONObject o = a.optJSONObject(i);
            if (o != null) out.add(o);
        }
        return out;
    }

    /* ------------------------------------------------------------ fechas */
    static Instant instante(String iso) {
        if (iso == null || iso.isEmpty()) return null;
        try { return OffsetDateTime.parse(iso).toInstant(); } catch (Exception ignorado) { /* sigue */ }
        try { return Instant.parse(iso); } catch (Exception ignorado) { /* sigue */ }
        try { return LocalDateTime.parse(iso).toInstant(ZoneOffset.UTC); } catch (Exception ignorado) { /* sigue */ }
        return null;
    }

    private static double horas(Instant i) {
        return (System.currentTimeMillis() - i.toEpochMilli()) / 3.6e6;
    }

    String fecha(String iso) {
        Instant i = instante(iso);
        return i == null ? "—" : fFecha.format(i).replace(".", "");
    }

    String hora(String iso) {
        Instant i = instante(iso);
        return i == null ? "—" : fHora.format(i);
    }

    static String hace(String iso) {
        Instant i = instante(iso);
        if (i == null) return "";
        double h = horas(i);
        if (h < 1) return "hace " + Math.max(1, Math.round(h * 60)) + " min";
        if (h < 48) return "hace " + Math.round(h) + " h";
        return "hace " + Math.round(h / 24) + " d";
    }

    static String duracion(String iso) {
        Instant i = instante(iso);
        if (i == null) return "";
        double h = horas(i);
        return h < 48 ? Math.max(1, Math.round(h)) + " h" : Math.round(h / 24) + " d";
    }

    /** ¿Los datos tienen más de 2,5 ciclos de antigüedad? (misma regla que la web) */
    boolean viejo() {
        Instant g = instante(s(d, "generado_utc"));
        if (g == null) return true;
        double cada = d.optDouble("cada_min", 60);
        if (Double.isNaN(cada) || cada <= 0) cada = 60;
        return horas(g) > cada / 60 * 2.5;
    }

    /* ------------------------------------------------------------ nombres */
    String corto(String lc) {
        JSONObject p = puertos.optJSONObject(lc);
        if (p == null) return lc;
        String c = s(p, "corto");
        return c.isEmpty() ? s(p, "nombre") : c;
    }

    private String nombrePuerto(String lc) {
        JSONObject p = puertos.optJSONObject(lc);
        String n = p == null ? "" : s(p, "nombre");
        return n.isEmpty() ? lc : n;
    }

    static String nombre(JSONObject b) {
        String n = s(b, "nombre");
        Matcher m = DIGITO_FINAL.matcher(n);
        return m.find() ? n.substring(0, m.start()) + " " + ROMANOS[m.group(1).charAt(0) - '0'] : n;
    }

    /* ------------------------------------------------------------ filas del widget */
    static final class Fila {
        final String nombre, resumen, imo;
        final int franja;
        final boolean aprox;

        Fila(String nombre, String resumen, String imo, int franja, boolean aprox) {
            this.nombre = nombre;
            this.resumen = resumen;
            this.imo = imo;
            this.franja = franja;
            this.aprox = aprox;
        }
    }

    private static String etaDe(JSONObject b) {
        String e = s(b, "eta_ais_utc");
        return e.isEmpty() ? s(b, "eta_aprox_utc") : e;
    }

    /** Vienen (por ETA), en puerto (atracados antes que en rada, por llegada) y zarparon (el más reciente primero). */
    List<Fila> filas(JSONObject p) {
        List<Fila> out = new ArrayList<>();
        List<JSONObject> vienen = lista(p, "vienen");
        vienen.sort(Comparator.comparing(b -> etaDe(b).isEmpty() ? "9" : etaDe(b)));
        for (JSONObject b : vienen) out.add(fila("vienen", b));
        List<JSONObject> enPuerto = lista(p, "en_puerto");
        enPuerto.sort(Comparator.<JSONObject, Boolean>comparing(b -> si(b, "en_rada")).thenComparing(b -> s(b, "llegada_utc")));
        for (JSONObject b : enPuerto) out.add(fila("en_puerto", b));
        List<JSONObject> zarparon = lista(p, "zarparon");
        zarparon.sort(Comparator.<JSONObject, String>comparing(b -> s(b, "salio_utc")).reversed());
        for (JSONObject b : zarparon) out.add(fila("zarparon", b));
        return out;
    }

    private Fila fila(String tipo, JSONObject b) {
        boolean aprox = "vienen".equals(tipo) && s(b, "eta_ais_utc").isEmpty() && !s(b, "eta_aprox_utc").isEmpty();
        return new Fila(nombre(b), resumen(tipo, b), s(b, "imo"), franja(tipo, b), aprox);
    }

    private String resumen(String tipo, JSONObject b) {
        switch (tipo) {
            case "vienen": {
                if (!s(b, "eta_ais_utc").isEmpty()) return "ETA " + fecha(s(b, "eta_ais_utc"));
                if (!s(b, "eta_aprox_utc").isEmpty()) return "ETA ≈ " + fecha(s(b, "eta_aprox_utc"));
                String de = s(b, "salio_de");
                return "de " + (de.isEmpty() ? "?" : de);
            }
            case "en_puerto":
                return (si(b, "en_rada") ? "en rada " : "atracado ") + duracion(s(b, "llegada_utc"));
            default: {
                String destino = s(b, "destino");
                return "zarpó " + hace(s(b, "salio_utc")) + (destino.isEmpty() ? "" : " → " + destino);
            }
        }
    }

    private static int franja(String tipo, JSONObject b) {
        if ("en_puerto".equals(tipo)) return si(b, "en_rada") ? R.drawable.franja_rada : R.drawable.franja_puerto;
        return "vienen".equals(tipo) ? R.drawable.franja_viene : R.drawable.franja_zarpo;
    }

    /* ------------------------------------------------------------ último movimiento */
    static final class Evento {
        final CharSequence texto;
        final String cuando;
        final int punto;

        Evento(CharSequence texto, String cuando, int punto) {
            this.texto = texto;
            this.cuando = cuando;
            this.punto = punto;
        }
    }

    Evento evento(JSONObject e) {
        String p = nombrePuerto(s(e, "puerto"));
        SpannableStringBuilder t = new SpannableStringBuilder();
        t.append(nombre(e), new StyleSpan(Typeface.BOLD), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        String cuando = s(e, "hora_utc").isEmpty() ? s(e, "utc") : s(e, "hora_utc");
        int punto;
        switch (s(e, "tipo")) {
            case "rumbo":
                t.append(" puso rumbo a ").append(p);
                if (!s(e, "origen").isEmpty()) t.append(" desde ").append(s(e, "origen"));
                punto = R.drawable.punto_viene;
                break;
            case "fondeo":
                t.append(" fondeó en la rada de ").append(p);
                punto = R.drawable.punto_rada;
                break;
            case "atraque":
                t.append(" atracó en ").append(p).append(si(e, "aprox") ? " (hora aprox.)" : "");
                punto = R.drawable.punto_puerto;
                break;
            case "zarpe":
                t.append(" zarpó de ").append(p);
                if (!s(e, "destino").isEmpty()) t.append(" rumbo a ").append(s(e, "destino"));
                punto = R.drawable.punto_zarpo;
                break;
            case "eta":
                t.append(": nuevo ETA a ").append(p).append(" ").append(fecha(s(e, "eta_utc")))
                        .append(" (antes ").append(fecha(s(e, "eta_previa_utc"))).append(")");
                cuando = s(e, "utc");
                punto = R.drawable.punto_falta;
                break;
            default:
                t.append(" ").append(s(e, "tipo"));
                punto = R.drawable.punto_zarpo;
        }
        return new Evento(t, cuando, punto);
    }
}
