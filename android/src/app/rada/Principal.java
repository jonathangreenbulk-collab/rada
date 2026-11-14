package app.rada;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.IOException;

/**
 * Panel completo: la misma interfaz web de Rada, empaquetada dentro de la app. La página no sale a
 * internet (todo lo que no sea la propia app se bloquea); los datos se los da la app ya descifrados.
 */
public final class Principal extends Activity {
    private static final String DOMINIO = "appassets.androidplatform.net";
    private static final String INICIO = "https://" + DOMINIO + "/web/index.html";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private FrameLayout raiz;
    private WebView web;
    private boolean primeraVez = true;
    private TextView vEstado;
    private Button vBoton;

    @Override
    protected void onCreate(Bundle guardado) {
        super.onCreate(guardado);
        raiz = new FrameLayout(this);
        raiz.setBackgroundColor(getColor(R.color.papel));
        raiz.setOnApplyWindowInsetsListener((v, ins) -> {
            Insets b = ins.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
            v.setPadding(b.left, b.top, b.right, b.bottom);
            return WindowInsets.CONSUMED;
        });
        setContentView(raiz);                                       // Android 15+: siempre de borde a borde
        barras();
        getOnBackInvokedDispatcher().registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::atras);
        atender(getIntent());
    }

    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        atender(i);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!primeraVez && web != null) {                           // al volver a la app: datos al día
            web.evaluateJavascript("(function(){var b=document.getElementById('b-recargar');if(b)b.click();})()", null);
        }
        primeraVez = false;
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    /** Qué hacer con lo que llega: una clave para vincular, o un destino (puerto o buque) desde el widget. */
    private void atender(Intent i) {
        String k = i.getStringExtra("clave");
        if (k != null) {
            i.removeExtra("clave");
            comprobar(k);
            return;
        }
        String destino = "";
        Uri u = i.getData();
        if (u != null && "rada".equals(u.getScheme())) {
            String d = u.getQueryParameter("d");
            if (d != null) destino = d;
        }
        if (Datos.clave(this) == null) vincular(null);
        else panel(destino);
    }

    /* ------------------------------------------------------------ panel (interfaz web empaquetada) */
    private void panel(String destino) {
        if (web == null) {
            vEstado = null;
            vBoton = null;
            web = new WebView(this);
            WebSettings s = web.getSettings();
            s.setJavaScriptEnabled(true);
            s.setDomStorageEnabled(true);
            s.setAllowFileAccess(false);
            s.setAllowContentAccess(false);
            s.setGeolocationEnabled(false);
            s.setSupportMultipleWindows(false);
            s.setMediaPlaybackRequiresUserGesture(true);
            web.setBackgroundColor(getColor(R.color.papel));
            web.addJavascriptInterface(new Puente(), "RadaAndroid");
            web.setWebViewClient(new Cliente());
            raiz.removeAllViews();
            raiz.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            web.loadUrl(destino.isEmpty() ? INICIO : INICIO + "#" + Uri.encode(destino));
        } else if (!destino.isEmpty()) {
            web.evaluateJavascript("location.hash='';location.hash=" + JSONObject.quote(destino) + ";", null);
        }
    }

    private final class Cliente extends WebViewClient {
        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (!"https".equals(u.getScheme()) || !DOMINIO.equals(u.getHost())) return respuesta(403, "Bloqueado");
            String ruta = u.getPath();
            if (ruta == null || ruta.contains("..")) return respuesta(403, "Bloqueado");
            if (ruta.startsWith("/")) ruta = ruta.substring(1);
            try {
                String tipo = tipo(ruta);
                return new WebResourceResponse(tipo, tipo.startsWith("text/") ? "utf-8" : null, getAssets().open(ruta));
            } catch (IOException e) {
                return respuesta(404, "No encontrado");
            }
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
            Uri u = r.getUrl();
            if (DOMINIO.equals(u.getHost())) return false;
            if ("https".equals(u.getScheme()) || "http".equals(u.getScheme())) {   // fichas externas: en el navegador
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u).addCategory(Intent.CATEGORY_BROWSABLE));
                } catch (ActivityNotFoundException ignorado) {
                    // sin navegador: no se abre
                }
            }
            return true;
        }
    }

    private static WebResourceResponse respuesta(int codigo, String motivo) {
        return new WebResourceResponse("text/plain", "utf-8", codigo, motivo, null, new ByteArrayInputStream(new byte[0]));
    }

    private static String tipo(String ruta) {
        String r = ruta.toLowerCase();
        if (r.endsWith(".html")) return "text/html";
        if (r.endsWith(".js")) return "text/javascript";
        if (r.endsWith(".css")) return "text/css";
        if (r.endsWith(".svg")) return "image/svg+xml";
        if (r.endsWith(".png")) return "image/png";
        if (r.endsWith(".woff2")) return "font/woff2";
        if (r.endsWith(".json")) return "application/json";
        return "application/octet-stream";
    }

    /** Lo único que la página puede pedirle a la app: los datos ya descifrados. */
    private final class Puente {
        @JavascriptInterface
        public void cargar(String id) {
            if (id == null || !id.matches("[a-z0-9]{1,40}")) return;
            Context app = getApplicationContext();
            new Thread(() -> {
                String json = Datos.obtener(app, 60_000L);
                ui.post(() -> {
                    if (web == null) return;
                    String q = JSONObject.quote(id);
                    web.evaluateJavascript("window.__rada&&window.__rada[" + q + "]&&window.__rada[" + q + "]("
                            + JSONObject.quote(json == null ? "" : json) + ")", null);
                });
            }, "rada-cargar").start();
        }
    }

    /* ------------------------------------------------------------ vincular (recibir la clave una vez) */
    private void vincular(String error) {
        if (web != null) {
            raiz.removeView(web);
            web.destroy();
            web = null;
        }
        raiz.removeAllViews();
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        int m = dp(24);
        l.setPadding(m, m, m, m);

        TextView titulo = texto("Vincular este teléfono", 28, R.color.tinta);
        titulo.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD));
        l.addView(titulo);
        l.addView(texto("Los datos viajan cifrados. Para abrirlos, este teléfono necesita la clave una sola vez: "
                + "conéctalo a la PC para recibirla, o pégala aquí.", 15, R.color.tinta_2), margen(dp(10)));

        vEstado = texto(error == null ? "" : error, 14, R.color.magenta);
        vEstado.setTypeface(Typeface.DEFAULT_BOLD);
        vEstado.setVisibility(error == null ? View.GONE : View.VISIBLE);
        l.addView(vEstado, margen(dp(14)));

        EditText campo = new EditText(this);
        campo.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        campo.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        campo.setSingleLine(true);
        campo.setHint("Clave");
        campo.setTextColor(getColor(R.color.tinta));
        campo.setHintTextColor(getColor(R.color.tinta_3));
        l.addView(campo, margen(dp(18)));

        vBoton = new Button(this);
        vBoton.setText("Guardar");
        vBoton.setOnClickListener(x -> comprobar(campo.getText().toString()));
        l.addView(vBoton, margen(dp(8)));

        ScrollView sv = new ScrollView(this);
        sv.setFillViewport(true);
        sv.addView(l);
        raiz.addView(sv);
    }

    /** Prueba la clave contra los datos reales; solo se guarda si los abre. */
    private void comprobar(String k) {
        if (!Datos.conFormato(k)) {
            fallo("Eso no parece una clave de Rada.");
            return;
        }
        if (vEstado == null) vincular(null);
        vEstado.setText("Comprobando la clave…");
        vEstado.setTextColor(getColor(R.color.tinta_2));
        vEstado.setVisibility(View.VISIBLE);
        vBoton.setEnabled(false);
        Context app = getApplicationContext();
        new Thread(() -> {
            Datos.Resultado r = Datos.descargar(app, k);
            ui.post(() -> {
                if (isFinishing() || isDestroyed()) return;
                if (r.ok) {
                    Toast.makeText(this, "Teléfono vinculado", Toast.LENGTH_SHORT).show();
                    Panel.pintarTodos(app, null);
                    panel("");
                } else {
                    fallo(r.claveMala ? "Esa clave no abre los datos."
                            : "No se pudo comprobar la clave (" + r.error + "). Revisa la conexión e inténtalo otra vez.");
                }
            });
        }, "rada-vincular").start();
    }

    /** Un intento fallido no borra una clave que ya funcionaba. */
    private void fallo(String mensaje) {
        if (Datos.clave(this) != null) {
            Toast.makeText(this, mensaje, Toast.LENGTH_LONG).show();
            panel("");
        } else {
            vincular(mensaje);
        }
    }

    /* ------------------------------------------------------------ utilidades */
    private void atras() {
        if (web == null) {
            moveTaskToBack(true);
            return;
        }
        web.evaluateJavascript("(function(){var d=document.getElementById('det');"
                + "if(d&&d.classList.contains('abierta')){d.classList.remove('abierta');return 1}return 0})()",
                r -> { if (!"1".equals(r)) moveTaskToBack(true); });
    }

    private void barras() {
        boolean noche = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        WindowInsetsController w = getWindow().getInsetsController();
        int claras = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
        if (w != null) w.setSystemBarsAppearance(noche ? 0 : claras, claras);
    }

    private TextView texto(String s, float sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(getColor(color));
        return t;
    }

    private LinearLayout.LayoutParams margen(int arriba) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.topMargin = arriba;
        return p;
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
