package app.rada;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;

/** Widget de pantalla de inicio: igual que el widget de la PC (puertos, cuentas, primeros buques y último movimiento). */
public final class Panel extends AppWidgetProvider {
    static final String ACTUALIZAR = "app.rada.ACTUALIZAR";

    @Override
    public void onUpdate(Context c, AppWidgetManager m, int[] ids) {
        for (int id : ids) m.updateAppWidget(id, vista(c, m.getAppWidgetOptions(id), null));
        Tarea.programar(c);
        if (System.currentTimeMillis() - Datos.ultimoIntento(c) > 25 * 60_000L) Tarea.ahora(c);
    }

    @Override
    public void onAppWidgetOptionsChanged(Context c, AppWidgetManager m, int id, Bundle opciones) {
        m.updateAppWidget(id, vista(c, opciones, null));            // cambió de tamaño: más o menos filas
    }

    @Override
    public void onEnabled(Context c) { Tarea.programar(c); }

    @Override
    public void onDisabled(Context c) { Tarea.cancelar(c); }       // ya no queda ningún widget

    @Override
    public void onReceive(Context c, Intent i) {
        super.onReceive(c, i);
        if (ACTUALIZAR.equals(i.getAction())) {
            pintarTodos(c, hayRed(c) ? "actualizando…" : "sin conexión");
            Tarea.ahora(c);
        }
    }

    static void pintarTodos(Context c, String estado) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        for (int id : m.getAppWidgetIds(new ComponentName(c, Panel.class))) {
            m.updateAppWidget(id, vista(c, m.getAppWidgetOptions(id), estado));
        }
    }

    /* ------------------------------------------------------------ dibujo */
    private static RemoteViews vista(Context c, Bundle opciones, String estado) {
        RemoteViews v = new RemoteViews(c.getPackageName(), R.layout.widget);
        v.setOnClickPendingIntent(android.R.id.background, abrir(c, ""));
        v.setOnClickPendingIntent(R.id.b_actualizar, actualizar(c));
        v.removeAllViews(R.id.puertos);

        JSONObject d = Datos.leerJson(c);
        boolean vinculado = Datos.clave(c) != null;
        if (!vinculado || d == null || d.optJSONObject("puertos") == null) {
            v.setViewVisibility(R.id.mensaje, View.VISIBLE);
            v.setViewVisibility(R.id.puertos, View.GONE);
            v.setViewVisibility(R.id.aviso, View.GONE);
            v.setViewVisibility(R.id.pie, View.GONE);
            v.setViewVisibility(R.id.pie_linea, View.GONE);
            v.setTextViewText(R.id.flota, "");
            v.setTextViewText(R.id.fresco, estado == null ? "" : estado);
            v.setTextViewText(R.id.mensaje, !vinculado ? "Toca aquí para vincular este teléfono."
                    : "Sin datos todavía. Toca ↻ para actualizar.");
            return v;
        }

        Texto t = new Texto(d);
        v.setViewVisibility(R.id.mensaje, View.GONE);
        v.setViewVisibility(R.id.puertos, View.VISIBLE);
        v.setTextViewText(R.id.flota, t.flota);

        boolean viejo = t.viejo();
        String hora = t.hora(Texto.s(d, "generado_utc"));
        v.setTextViewText(R.id.fresco, estado != null ? estado : viejo ? "sin actualizar desde " + hora : "act. " + hora);
        v.setColorStateList(R.id.fresco, "setTextColor", viejo && estado == null ? R.color.magenta : R.color.tinta_2);

        List<String> avisos = new ArrayList<>();
        String error = Datos.ultimoError(c);
        if ("clave".equals(error)) avisos.add("La clave ya no abre los datos: vuelve a vincular el teléfono");
        else if (error != null && viejo) avisos.add("Sin conexión con la nube: datos guardados");
        JSONArray av = d.optJSONArray("avisos");
        if (av != null) for (int i = 0; i < av.length(); i++) {
            JSONObject a = av.optJSONObject(i);
            if (a != null) avisos.add(Texto.s(a, "sitio") + " en pausa hasta " + t.hora(Texto.s(a, "hasta_utc")));
        }
        v.setViewVisibility(R.id.aviso, avisos.isEmpty() ? View.GONE : View.VISIBLE);
        v.setTextViewText(R.id.aviso, String.join(" · ", avisos));

        int filas = filasPara(opciones, !avisos.isEmpty());
        JSONObject puertos = d.optJSONObject("puertos");
        for (Iterator<String> it = puertos.keys(); it.hasNext(); ) {
            String lc = it.next();
            JSONObject p = puertos.optJSONObject(lc);
            if (p != null) v.addView(R.id.puertos, columna(c, t, lc, p, filas));
        }

        v.setViewVisibility(R.id.pie, View.VISIBLE);
        v.setViewVisibility(R.id.pie_linea, View.VISIBLE);
        JSONArray eventos = d.optJSONArray("eventos");
        JSONObject e = eventos != null ? eventos.optJSONObject(0) : null;
        if (e != null) {
            Texto.Evento x = t.evento(e);
            v.setImageViewResource(R.id.pie_punto, x.punto);
            v.setTextViewText(R.id.pie_texto, x.texto);
            v.setTextViewText(R.id.pie_hora, Texto.hace(x.cuando));
        } else {
            v.setImageViewResource(R.id.pie_punto, R.drawable.punto_zarpo);
            v.setTextViewText(R.id.pie_texto, "Sin movimientos registrados todavía");
            v.setTextViewText(R.id.pie_hora, "");
        }
        return v;
    }

    private static RemoteViews columna(Context c, Texto t, String lc, JSONObject p, int max) {
        String pkg = c.getPackageName();
        RemoteViews col = new RemoteViews(pkg, R.layout.columna);
        col.setTextViewText(R.id.col_nombre, t.corto(lc));
        col.setOnClickPendingIntent(R.id.col_nombre, abrir(c, lc));
        cuenta(col, R.id.n_vienen, Texto.largo(p, "vienen"), R.color.viene);
        cuenta(col, R.id.n_puerto, Texto.largo(p, "en_puerto"), R.color.puerto);
        cuenta(col, R.id.n_zarpo, Texto.largo(p, "zarparon"), R.color.zarpo);

        List<Texto.Fila> lista = t.filas(p);
        col.removeAllViews(R.id.filas);
        for (int i = 0; i < Math.min(max, lista.size()); i++) {
            Texto.Fila f = lista.get(i);
            RemoteViews r = new RemoteViews(pkg, R.layout.fila);
            r.setImageViewResource(R.id.franja, f.franja);
            r.setTextViewText(R.id.fila_nombre, f.nombre);
            r.setTextViewText(R.id.fila_resumen, f.resumen);
            if (f.aprox) r.setColorStateList(R.id.fila_resumen, "setTextColor", R.color.magenta);
            r.setOnClickPendingIntent(R.id.fila, abrir(c, "imo-" + f.imo));
            col.addView(R.id.filas, r);
        }
        if (lista.isEmpty()) {
            col.setViewVisibility(R.id.col_vacio, View.VISIBLE);
            col.setTextViewText(R.id.col_vacio, "Sin buques" + (t.flota.isEmpty() ? "" : " " + t.flota) + " ahora");
        }
        if (lista.size() > max) {
            col.setViewVisibility(R.id.col_mas, View.VISIBLE);
            col.setTextViewText(R.id.col_mas, "+" + (lista.size() - max) + " más");
            col.setOnClickPendingIntent(R.id.col_mas, abrir(c, lc));
        }
        return col;
    }

    private static void cuenta(RemoteViews v, int id, int n, int color) {
        v.setTextViewText(id, String.valueOf(n));
        v.setColorStateList(id, "setTextColor", n > 0 ? color : R.color.cero);
    }

    /** Cuántos buques caben por columna según el alto del widget (en dp). */
    private static int filasPara(Bundle o, boolean conAviso) {
        int alto = o != null ? o.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0) : 0;
        if (alto <= 0) alto = 220;
        int libre = alto - 42 /* cabecera */ - 32 /* pie */ - 20 /* puerto */ - 36 /* cuentas */ - (conAviso ? 18 : 0) - 6;
        return Math.max(1, Math.min(8, libre / 38));
    }

    /* ------------------------------------------------------------ toques */
    static PendingIntent abrir(Context c, String destino) {
        Intent i = new Intent(c, Principal.class)
                .setAction(Intent.ACTION_VIEW)
                .setData(new Uri.Builder().scheme("rada").authority("abrir").appendQueryParameter("d", destino).build())
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return PendingIntent.getActivity(c, 0, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private static PendingIntent actualizar(Context c) {
        Intent i = new Intent(c, Panel.class).setAction(ACTUALIZAR);
        return PendingIntent.getBroadcast(c, 0, i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private static boolean hayRed(Context c) {
        ConnectivityManager cm = c.getSystemService(ConnectivityManager.class);
        if (cm == null) return true;
        Network n = cm.getActiveNetwork();
        NetworkCapabilities nc = n == null ? null : cm.getNetworkCapabilities(n);
        return nc != null && nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }
}
