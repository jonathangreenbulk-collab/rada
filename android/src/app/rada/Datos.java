package app.rada;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.GeneralSecurityException;
import java.util.Base64;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

import javax.crypto.AEADBadTagException;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Datos de Rada: baja estado.enc de la rama 'datos', lo descifra (AES-256-GCM sobre zlib, el mismo
 * formato que scripts/cifrado.py) y guarda el JSON en el almacenamiento privado de la app.
 * La clave vive en las preferencias privadas: no se escribe en registros ni se respalda.
 */
final class Datos {
    private Datos() {}

    private static final String PREFS = "rada";
    private static final byte[] MAGIA = "RADA1".getBytes(StandardCharsets.US_ASCII);
    private static final int MAX_DESCARGA = 8 << 20, MAX_JSON = 32 << 20;
    private static final Object CANDADO = new Object();

    /** Resultado de un intento de descarga. */
    static final class Resultado {
        final boolean ok, cambio, claveMala;
        final String error;

        Resultado(boolean ok, boolean cambio, boolean claveMala, String error) {
            this.ok = ok;
            this.cambio = cambio;
            this.claveMala = claveMala;
            this.error = error;
        }
    }

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static boolean conFormato(String k) {
        return k != null && k.trim().matches("[A-Za-z0-9_-]{43}=?");
    }

    static String clave(Context c) {
        String k = prefs(c).getString("clave", null);
        return conFormato(k) ? k : null;
    }

    static long ultimoIntento(Context c) { return prefs(c).getLong("intento", 0L); }

    /** null si el último intento salió bien; "clave" si la clave guardada ya no abre los datos. */
    static String ultimoError(Context c) { return prefs(c).getString("error", null); }

    private static String url() {
        return "https://raw.githubusercontent.com/" + Config.REPO + "/datos/estado.enc";
    }

    private static File archivo(Context c) { return new File(c.getFilesDir(), "estado.json"); }

    static String leer(Context c) {
        File f = archivo(c);
        if (!f.isFile()) return null;
        try {
            return new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8);
        } catch (IOException e) {
            return null;
        }
    }

    static JSONObject leerJson(Context c) {
        String s = leer(c);
        if (s == null) return null;
        try {
            return new JSONObject(s);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Descarga y descifra. Con {@code claveNueva} prueba esa clave y solo la guarda si abre los datos
     * de verdad (así nadie puede cambiarla por otra cualquiera).
     */
    static Resultado descargar(Context c, String claveNueva) {
        synchronized (CANDADO) {
            SharedPreferences p = prefs(c);
            boolean prueba = claveNueva != null;
            String k = prueba ? claveNueva.trim() : clave(c);
            if (!conFormato(k)) return new Resultado(false, false, prueba, "sin clave");
            long ahora = System.currentTimeMillis();
            if (!prueba) p.edit().putLong("intento", ahora).apply();
            HttpURLConnection con = null;
            try {
                con = (HttpURLConnection) new URL(url()).openConnection();
                con.setConnectTimeout(15_000);
                con.setReadTimeout(20_000);
                con.setUseCaches(false);
                con.setRequestProperty("User-Agent", "rada-android");
                String etag = p.getString("etag", null);
                if (!prueba && etag != null && archivo(c).isFile()) con.setRequestProperty("If-None-Match", etag);
                int codigo = con.getResponseCode();
                if (codigo == HttpURLConnection.HTTP_NOT_MODIFIED) {
                    p.edit().putLong("ok", ahora).remove("error").apply();
                    return new Resultado(true, false, false, null);
                }
                if (codigo != HttpURLConnection.HTTP_OK) throw new IOException("GitHub respondió " + codigo);
                byte[] blob;
                try (InputStream in = con.getInputStream()) {
                    blob = leerTodo(in, MAX_DESCARGA);
                }
                byte[] json = descifrar(blob, bytesClave(k), "estado");
                new JSONObject(new String(json, StandardCharsets.UTF_8));      // que sea JSON válido antes de guardarlo
                escribir(archivo(c), json);
                SharedPreferences.Editor e = p.edit().putLong("ok", ahora).putLong("intento", ahora).remove("error");
                String nueva = con.getHeaderField("ETag");
                if (nueva != null) e.putString("etag", nueva); else e.remove("etag");
                if (prueba) e.putString("clave", k);
                e.commit();
                return new Resultado(true, true, false, null);
            } catch (AEADBadTagException e) {                                 // la clave no corresponde a los datos
                if (!prueba) p.edit().putString("error", "clave").apply();
                return new Resultado(false, false, true, "clave");
            } catch (Exception e) {
                String m = mensaje(e);
                if (!prueba) p.edit().putString("error", m).apply();
                return new Resultado(false, false, false, m);
            } finally {
                if (con != null) con.disconnect();
            }
        }
    }

    /** Para la app: los datos guardados, bajando antes una versión nueva si la última consulta fue hace más de maxEdadMs. */
    static String obtener(Context c, long maxEdadMs) {
        if (clave(c) != null && System.currentTimeMillis() - ultimoIntento(c) > maxEdadMs) {
            Resultado r = descargar(c, null);
            if (r.cambio || !r.ok) Panel.pintarTodos(c, null);
        }
        return leer(c);
    }

    static byte[] descifrar(byte[] blob, byte[] clave, String etiqueta) throws GeneralSecurityException, DataFormatException {
        int n = MAGIA.length;
        if (blob.length < n + 12 + 16) throw new DataFormatException("archivo incompleto");
        for (int i = 0; i < n; i++) if (blob[i] != MAGIA[i]) throw new DataFormatException("no es un archivo de Rada");
        Cipher ci = Cipher.getInstance("AES/GCM/NoPadding");
        ci.init(Cipher.DECRYPT_MODE, new SecretKeySpec(clave, "AES"), new GCMParameterSpec(128, blob, n, 12));
        ci.updateAAD(etiqueta.getBytes(StandardCharsets.UTF_8));
        byte[] comprimido = ci.doFinal(blob, n + 12, blob.length - n - 12);
        Inflater inf = new Inflater();                                      // zlib con cabecera, como zlib.compress
        try {
            inf.setInput(comprimido);
            ByteArrayOutputStream out = new ByteArrayOutputStream(comprimido.length * 4);
            byte[] buf = new byte[16384];
            while (!inf.finished()) {
                int leidos = inf.inflate(buf);
                if (leidos == 0 && (inf.needsInput() || inf.needsDictionary())) throw new DataFormatException("datos incompletos");
                out.write(buf, 0, leidos);
                if (out.size() > MAX_JSON) throw new DataFormatException("datos demasiado grandes");
            }
            return out.toByteArray();
        } finally {
            inf.end();
        }
    }

    private static byte[] bytesClave(String k) {
        byte[] b = Base64.getUrlDecoder().decode(k.trim().replace("=", ""));
        if (b.length != 32) throw new IllegalArgumentException("la clave debe tener 256 bits");
        return b;
    }

    private static byte[] leerTodo(InputStream in, int max) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
            if (out.size() > max) throw new IOException("archivo demasiado grande");
        }
        return out.toByteArray();
    }

    private static void escribir(File f, byte[] datos) throws IOException {
        File tmp = new File(f.getPath() + ".tmp");
        try (FileOutputStream o = new FileOutputStream(tmp)) {
            o.write(datos);
            o.getFD().sync();
        }
        if (!tmp.renameTo(f)) throw new IOException("no se pudo guardar");
    }

    private static String mensaje(Exception e) {
        if (e instanceof UnknownHostException) return "sin conexión";
        if (e instanceof SocketTimeoutException) return "la conexión tardó demasiado";
        if (e instanceof IOException && e.getMessage() != null && e.getMessage().startsWith("GitHub")) return e.getMessage();
        return e.getClass().getSimpleName();
    }
}
