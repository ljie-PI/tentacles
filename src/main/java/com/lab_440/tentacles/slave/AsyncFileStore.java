package com.lab_440.tentacles.slave;

import io.vertx.core.Handler;
import io.vertx.core.Vertx;
import io.vertx.core.buffer.Buffer;
import io.vertx.core.file.AsyncFile;
import io.vertx.core.file.OpenOptions;
import io.vertx.core.streams.Pump;
import io.vertx.core.streams.ReadStream;

public class AsyncFileStore {

    private Vertx vertx;

    public AsyncFileStore(Vertx vertx) {
        this.vertx = vertx;
    }

    /**
     * Asynchronously store content from source to filePath,
     * and call onEnd when finished
     * @param source
     * @param filePath
     * @param onEnd
     */
    public void asyncStore(ReadStream<Buffer> source,
                           String filePath,
                           Handler<Void> onEnd) {
        source.pause();
        vertx.fileSystem().open(filePath,
                new OpenOptions(),
                fres -> {
                    AsyncFile afile = fres.result();
                    Pump pump = Pump.pump(source, afile);
                    source.endHandler(onEnd);
                    pump.start();
                    source.resume();
                }
        );
    }
}
