package com.lab_440.tentacles.common;

import io.vertx.core.json.JsonObject;
import org.junit.Assert;
import org.junit.Test;

public class ConfigurationTest {

    // default common configurations
    private final String DEFAULT_ROLE = Configuration.Role.SLAVE.toString();
    private final String DEFAULT_MASTER_HOST = "127.0.0.1";
    private final int DEFAULT_MASTER_PORT = 8080;
    private final String DEFAULT_REDIS_HOST = "127.0.0.1";
    private final int DEFAULT_REDIS_PORT = 6379;
    private final String DEFAULT_REDIS_PASSWD = null;
    // default master configurations
    private final int DEFAULT_MASTER_INSTANCE_NUM = 1;
    private final String DEFAULT_DUPCHECKER_CLASS = "RedisDupChecker";
    private final String DEFAULT_SCHEDULER_CLASS = "RedisScheduler";
    private final String DEFAULT_DATASTORE_CLASS = "RedisDatastore";
    // default slave configurations
    private final String DEFAULT_CRAWLER_REGISTRATION_CLASS
            = "com.lab_440.tentacles.slave.registration.BaseRegistration";
    private final int DEFAULT_URL_BATCH_SIZE = 1;
    private final int DEFAULT_URL_CHECK_INTERVAL = 10;
    private final int DEFAULT_URL_FETCH_INTERVAL = 30;
    private final int DEFAULT_EXECUTOR_INSTANCE_NUM = 1;
    private final int DEFAULT_PROXY_CHANGE_INTERVAL = 600;
    private final int DEFAULT_DOMAIN_QUEUE_SIZE = 0;
    private final String DEFAULT_FILESTORE_PATH = "/tmp/tentacles/files";
    private final int DEFAULT_DOWNLOAD_INTERVAL = 100; // milliseconds
    private final int DEFAULT_RETRY_TIMES = 2;

    @Test
    public void testDefaultConfiguration() {
        Configuration conf = new Configuration();
        Assert.assertEquals(DEFAULT_ROLE, conf.getRole().toString());
        Assert.assertEquals(DEFAULT_MASTER_HOST, conf.getMasterHost());
        Assert.assertEquals(DEFAULT_MASTER_PORT, conf.getMasterPort());
        Assert.assertEquals(DEFAULT_REDIS_HOST, conf.getRedisHost());
        Assert.assertEquals(DEFAULT_REDIS_PORT, conf.getRedisPort());

        Assert.assertEquals(DEFAULT_MASTER_INSTANCE_NUM, conf.getMasterNum());
        Assert.assertEquals(DEFAULT_DUPCHECKER_CLASS, conf.getDupCheckerClass());
        Assert.assertEquals(DEFAULT_SCHEDULER_CLASS, conf.getSchedulerClass());
        Assert.assertEquals(DEFAULT_DATASTORE_CLASS, conf.getDataStoreClass());

        Assert.assertEquals(DEFAULT_CRAWLER_REGISTRATION_CLASS, conf.getCrawlerRegistration());
        Assert.assertEquals(DEFAULT_URL_BATCH_SIZE, conf.getUrlBatchSize());
        Assert.assertEquals(DEFAULT_URL_CHECK_INTERVAL, conf.getUrlCheckInterval());
        Assert.assertEquals(DEFAULT_URL_FETCH_INTERVAL, conf.getUrlFetchInterval());
        Assert.assertEquals(DEFAULT_EXECUTOR_INSTANCE_NUM, conf.getExecutorNum());
        Assert.assertEquals(DEFAULT_PROXY_CHANGE_INTERVAL, conf.getProxyChangeInterval());
        Assert.assertEquals(DEFAULT_DOMAIN_QUEUE_SIZE, conf.getDomainQueueSize());
        Assert.assertEquals(DEFAULT_FILESTORE_PATH, conf.getFileStorePath());
        Assert.assertEquals(DEFAULT_DOWNLOAD_INTERVAL, conf.getDownloadInterval());
        Assert.assertEquals(DEFAULT_RETRY_TIMES, conf.getRetryTimes());
    }

    @Test
    public void testLoadConfigFromFile() {
        Configuration conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/master_test.properties"
        );
        Assert.assertEquals("MASTER", conf.getRole().toString());
        Assert.assertEquals(8080, conf.getMasterPort());
    }

    @Test
    public void testToJsonObject() {
        Configuration conf = new Configuration(
                System.getProperty("user.dir") + "/src/test/resources/slave_test.properties"
        );
        Assert.assertEquals("SLAVE", conf.getRole().toString());
        Assert.assertEquals(8080, conf.getMasterPort());
        JsonObject jObj = conf.toJsonObject();
        Assert.assertEquals("SLAVE", jObj.getString("role"));
        Assert.assertEquals(new Integer(8080), jObj.getInteger("master.port"));
        Configuration newConf = new Configuration(jObj);
        Assert.assertEquals("SLAVE", newConf.getRole().toString());
        Assert.assertEquals(8080, newConf.getMasterPort());
        Assert.assertEquals(100, newConf.getDownloadInterval());
    }
}
