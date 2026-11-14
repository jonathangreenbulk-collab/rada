package app.rada;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

/**
 * Actualización en segundo plano: cada 30 minutos con internet (sobrevive a reinicios) y una
 * inmediata cuando se toca ↻ en el widget. Solo baja datos si hay un widget o la app los pide.
 */
public final class Tarea extends JobService {
    private static final int PERIODICA = 1, AHORA = 2;

    static void programar(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js == null || js.getPendingJob(PERIODICA) != null) return;
        js.schedule(new JobInfo.Builder(PERIODICA, new ComponentName(c, Tarea.class))
                .setPeriodic(30 * 60_000L, 10 * 60_000L)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build());
    }

    static void cancelar(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js != null) js.cancel(PERIODICA);
    }

    static void ahora(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js == null) return;
        ComponentName cn = new ComponentName(c, Tarea.class);
        int r;
        try {
            r = js.schedule(new JobInfo.Builder(AHORA, cn)
                    .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                    .setExpedited(true)
                    .build());
        } catch (RuntimeException e) {
            r = JobScheduler.RESULT_FAILURE;
        }
        if (r != JobScheduler.RESULT_SUCCESS) {                       // sin cupo de tareas urgentes: normal
            js.schedule(new JobInfo.Builder(AHORA, cn).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).build());
        }
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        Context app = getApplicationContext();
        boolean periodica = params.getJobId() == PERIODICA;
        new Thread(() -> {
            try {
                // la periódica no repite si la app acaba de bajar datos
                if (!periodica || System.currentTimeMillis() - Datos.ultimoIntento(app) > 5 * 60_000L) {
                    Datos.descargar(app, null);
                }
                Panel.pintarTodos(app, null);
            } finally {
                jobFinished(params, false);
            }
        }, "rada-tarea").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;                                                    // el sistema la cortó: que la reintente
    }
}
