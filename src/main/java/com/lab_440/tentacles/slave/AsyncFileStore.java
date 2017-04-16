package com.lab_440.tentacles.slave;

import io.vertx.core.Handler;
import io.vertx.core.Vertx;
import io.vertx.core.buffer.Buffer;
import io.vertx.core.file.AsyncFile;
import io.vertx.core.file.OpenOptions;
import io.vertx.core.streams.Pump;
import io.vertx.core.streams.ReadStream;

import java.io.File;
import java.nio.file.Path;

public class AsyncFileStore {

    /**
     * Asynchronously store content from source to filePath,
     * and call onEnd when finished
     * @param source
     * @param filePath
     * @param onEnd
     */
    public static void asyncStore(Vertx vertx,
                           ReadStream<Buffer> source,
                           String filePath,
                           Handler<Void> onEnd) {
        checkDir(filePath);
        source.pause();
        vertx.fileSystem().open(filePath,
                new OpenOptions().setWrite(true).setCreate(true),
                fres -> {
                    AsyncFile afile = fres.result();
                    Pump pump = Pump.pump(source, afile);
                    source.endHandler(onEnd);
                    pump.start();
                    source.resume();
                });
    }

    private static void checkDir(String filePath) {
        String dirStr = filePath.substring(0, filePath.lastIndexOf("/"));
        File dir = new File(dirStr);
        synchronized (AsyncFileStore.class) {
            if (!dir.exists()) {
                dir.mkdirs();
            }
        }
    }
}
