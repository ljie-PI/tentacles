package com.lab_440.tentacles;

import com.lab_440.tentacles.common.RemoteCall;
import io.vertx.core.Vertx;
import io.vertx.ext.unit.Async;
import io.vertx.ext.unit.TestContext;
import io.vertx.ext.unit.junit.VertxUnitRunner;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(VertxUnitRunner.class)
public class CrawlerControllerTest {

    @Test(timeout = 3000)
    public void testLaunchSlaveFirstCauseFailure(TestContext context) {
        Async async = context.async();
        Configuration conf = new Configuration();
        RemoteCall rc = new RemoteCall(Vertx.vertx(), conf.getMasterHost(), conf.getMasterPort());
        rc.get(RemoteCall.CHECK_MASTER_STATUS_URI,
                resp -> context.fail(),
                err -> {
                    context.assertTrue(err.getMessage().indexOf("Connection refused") >= 0);
                    async.complete();
                }
        );
    }
}
