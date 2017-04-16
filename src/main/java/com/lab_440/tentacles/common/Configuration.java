package com.lab_440.tentacles.common;

import io.vertx.core.json.JsonObject;
import io.vertx.core.logging.Logger;
import io.vertx.core.logging.LoggerFactory;

import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.util.Properties;

/**
 * Configurations
 */
public class Configuration {

    private Logger logger = LoggerFactory.getLogger(getClass());

    // common configuration fields
    private final String ROLE_FIELD = "role";
    private final String MASTER_HOST_FIELD = "master.host";
    private final String MASTER_PORT_FIELD = "master.port";
    private final String REDIS_HOST_FIELD = "redis.host";
    private final String REDIS_PORT_FIELD = "redis.port";
    private final String REDIS_PASSWD_FIELD = "redis.passwd";
    // master configuration fields
    private final String MASTER_INSTANCE_NUM_FIELD = "master.instance.number";
    private final String DUPCHECKER_CLASS_FIELD = "dupchecker.class";
    private final String SCHEDULER_CLASS_FIELD = "scheduler.class";
    private final String DATASTORE_CLASS_FIELD = "datastore.class";
    // slave configuration fields
    private final String CRAWLER_REGISTRATION_CLASS_FIELD = "crawler.registration.class";
    private final String URL_BATCH_SIZE_FIELD = "url.batch.size";
    private final String URL_CHECK_INTERVAL_FIELD = "url.check.interval";
    private final String URL_FETCH_INTERVAL_FIELD = "url.fetch.interval";
    private final String EXECUTOR_INSTANCE_NUM_FIELD = "executor.instance.number";
    private final String PROXY_CHANGE_INTERVAL_FIELD = "proxy.change.interval";
    private final String DOMAIN_QUEUE_SIZE_FIELD = "domain.queue.size";
    private final String FILESTORE_PATH_FIELD = "file.store.path";
    private final String DOWNLOAD_INTERVAL_FIELD = "download.interval";
    private final String RETRY_TIMES_FIELD = "retry.times";

    // default common configurations
    private final String DEFAULT_ROLE = Role.SLAVE.toString();
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

    // common configurations
    private Role role;
    private String masterHost;
    private int masterPort;
    private String redisHost;
    private int redisPort;
    private String redisPasswd;
    // master configurations
    private int masterNum;
    private String dupCheckerClass;
    private String schedulerClass;
    private String dataStoreClass;
    // slave configurations
    private String crawlerRegistration;
    private int urlBatchSize;
    private int urlCheckInterval;
    private int urlFetchInterval;
    private int executorNum;
    private int proxyChangeInterval;
    private int domainQueueSize;
    private String fileStorePath;
    private int downloadInterval;
    private int retryTimes;

    public Configuration() {
        fromJsonObject(new JsonObject());
    }

    public Configuration(JsonObject jObj) {
        fromJsonObject(jObj);
    }

    public Configuration(String configPath) {
        try {
            File configFile = new File(configPath);
            Properties prop = loadConfigs(configFile);

            String sRole = prop.getProperty(ROLE_FIELD, DEFAULT_ROLE);
            role = sRole.toUpperCase().equals(Role.MASTER.toString()) ? Role.MASTER : Role.SLAVE;
            masterHost = prop.getProperty(MASTER_HOST_FIELD, DEFAULT_MASTER_HOST);
            masterPort = getIntProperty(prop, MASTER_PORT_FIELD, DEFAULT_MASTER_PORT);
            redisHost = prop.getProperty(REDIS_HOST_FIELD, DEFAULT_REDIS_HOST);
            redisPort = getIntProperty(prop, REDIS_PORT_FIELD, DEFAULT_REDIS_PORT);
            redisPasswd = prop.getProperty(REDIS_PASSWD_FIELD, DEFAULT_REDIS_PASSWD);

            masterNum = getIntProperty(prop, MASTER_INSTANCE_NUM_FIELD, DEFAULT_MASTER_INSTANCE_NUM);
            dupCheckerClass = prop.getProperty(DUPCHECKER_CLASS_FIELD, DEFAULT_DUPCHECKER_CLASS);
            schedulerClass = prop.getProperty(SCHEDULER_CLASS_FIELD, DEFAULT_SCHEDULER_CLASS);
            dataStoreClass = prop.getProperty(DATASTORE_CLASS_FIELD, DEFAULT_DATASTORE_CLASS);

            crawlerRegistration = prop.getProperty(CRAWLER_REGISTRATION_CLASS_FIELD, DEFAULT_CRAWLER_REGISTRATION_CLASS);
            urlBatchSize = getIntProperty(prop, URL_BATCH_SIZE_FIELD, DEFAULT_URL_BATCH_SIZE);
            urlCheckInterval = getIntProperty(prop, URL_CHECK_INTERVAL_FIELD, DEFAULT_URL_CHECK_INTERVAL);
            urlFetchInterval = getIntProperty(prop, URL_FETCH_INTERVAL_FIELD, DEFAULT_URL_FETCH_INTERVAL);
            executorNum = getIntProperty(prop, EXECUTOR_INSTANCE_NUM_FIELD, DEFAULT_EXECUTOR_INSTANCE_NUM);
            proxyChangeInterval = getIntProperty(prop, PROXY_CHANGE_INTERVAL_FIELD, DEFAULT_PROXY_CHANGE_INTERVAL);
            domainQueueSize = getIntProperty(prop, DOMAIN_QUEUE_SIZE_FIELD, DEFAULT_DOMAIN_QUEUE_SIZE);
            fileStorePath = prop.getProperty(FILESTORE_PATH_FIELD, DEFAULT_FILESTORE_PATH);
            downloadInterval = getIntProperty(prop, DOWNLOAD_INTERVAL_FIELD, DEFAULT_DOWNLOAD_INTERVAL);
            retryTimes = getIntProperty(prop, RETRY_TIMES_FIELD, DEFAULT_RETRY_TIMES);
        } catch (IOException ioe) {
            logger.error("Failed to load configurations! {}", ioe.getMessage());
            System.exit(1);
        }
    }


    public JsonObject toJsonObject() {
        return new JsonObject()
                .put(ROLE_FIELD, role)
                .put(MASTER_HOST_FIELD, masterHost)
                .put(MASTER_PORT_FIELD, masterPort)
                .put(REDIS_HOST_FIELD, redisHost)
                .put(REDIS_PORT_FIELD, redisPort)
                .put(REDIS_PASSWD_FIELD, redisPasswd)

                .put(MASTER_INSTANCE_NUM_FIELD, masterNum)
                .put(DUPCHECKER_CLASS_FIELD, dupCheckerClass)
                .put(SCHEDULER_CLASS_FIELD, schedulerClass)
                .put(DATASTORE_CLASS_FIELD, dataStoreClass)

                .put(CRAWLER_REGISTRATION_CLASS_FIELD, crawlerRegistration)
                .put(URL_BATCH_SIZE_FIELD, urlBatchSize)
                .put(URL_CHECK_INTERVAL_FIELD, urlCheckInterval)
                .put(URL_FETCH_INTERVAL_FIELD, urlFetchInterval)
                .put(EXECUTOR_INSTANCE_NUM_FIELD, executorNum)
                .put(PROXY_CHANGE_INTERVAL_FIELD, proxyChangeInterval)
                .put(DOMAIN_QUEUE_SIZE_FIELD, domainQueueSize)
                .put(FILESTORE_PATH_FIELD, fileStorePath)
                .put(DOWNLOAD_INTERVAL_FIELD, downloadInterval)
                .put(RETRY_TIMES_FIELD, retryTimes);
    }

    public Configuration fromJsonObject(JsonObject jObj) {
        String roleStr = jObj.getString(ROLE_FIELD, Role.SLAVE.toString());
        if (roleStr.equals(Role.MASTER.toString())) {
            role = Role.MASTER;
        } else {
            role = Role.SLAVE;
        }
        masterHost = jObj.getString(MASTER_HOST_FIELD, DEFAULT_MASTER_HOST);
        masterPort = jObj.getInteger(MASTER_PORT_FIELD, DEFAULT_MASTER_PORT);
        redisHost = jObj.getString(REDIS_HOST_FIELD, DEFAULT_REDIS_HOST);
        redisPort = jObj.getInteger(REDIS_PORT_FIELD, DEFAULT_REDIS_PORT);
        redisPasswd = jObj.getString(REDIS_PASSWD_FIELD, DEFAULT_REDIS_PASSWD);

        masterNum = jObj.getInteger(MASTER_INSTANCE_NUM_FIELD, DEFAULT_MASTER_INSTANCE_NUM);
        dupCheckerClass = jObj.getString(DUPCHECKER_CLASS_FIELD, DEFAULT_DUPCHECKER_CLASS);
        schedulerClass = jObj.getString(SCHEDULER_CLASS_FIELD, DEFAULT_SCHEDULER_CLASS);
        dataStoreClass = jObj.getString(DATASTORE_CLASS_FIELD, DEFAULT_DATASTORE_CLASS);

        crawlerRegistration = jObj.getString(CRAWLER_REGISTRATION_CLASS_FIELD, DEFAULT_CRAWLER_REGISTRATION_CLASS);
        urlBatchSize = jObj.getInteger(URL_BATCH_SIZE_FIELD, DEFAULT_URL_BATCH_SIZE);
        urlCheckInterval = jObj.getInteger(URL_CHECK_INTERVAL_FIELD, DEFAULT_URL_CHECK_INTERVAL);
        urlFetchInterval = jObj.getInteger(URL_FETCH_INTERVAL_FIELD, DEFAULT_URL_FETCH_INTERVAL);
        executorNum = jObj.getInteger(EXECUTOR_INSTANCE_NUM_FIELD, DEFAULT_EXECUTOR_INSTANCE_NUM);
        proxyChangeInterval = jObj.getInteger(PROXY_CHANGE_INTERVAL_FIELD, DEFAULT_PROXY_CHANGE_INTERVAL);
        domainQueueSize = jObj.getInteger(DOMAIN_QUEUE_SIZE_FIELD, DEFAULT_DOMAIN_QUEUE_SIZE);
        fileStorePath = jObj.getString(FILESTORE_PATH_FIELD, DEFAULT_FILESTORE_PATH);
        downloadInterval = jObj.getInteger(DOWNLOAD_INTERVAL_FIELD, DEFAULT_DOWNLOAD_INTERVAL);
        retryTimes = jObj.getInteger(RETRY_TIMES_FIELD, DEFAULT_RETRY_TIMES);
        return this;
    }

    public Role getRole() {
        return role;
    }

    public String getMasterHost() {
        return masterHost;
    }

    public int getMasterPort() {
        return masterPort;
    }

    public int getMasterNum() {
        return masterNum;
    }

    public String getRedisHost() {
        return redisHost;
    }

    public int getRedisPort() {
        return redisPort;
    }

    public String getRedisPasswd() {
        return redisPasswd;
    }

    public String getDupCheckerClass() {
        return dupCheckerClass;
    }

    public String getSchedulerClass() {
        return schedulerClass;
    }

    public String getDataStoreClass() {
        return dataStoreClass;
    }

    public String getCrawlerRegistration() {
        return crawlerRegistration;
    }

    public int getUrlBatchSize() {
        return urlBatchSize;
    }

    public int getUrlCheckInterval() {
        return urlCheckInterval;
    }

    public int getUrlFetchInterval() {
        return urlFetchInterval;
    }

    public int getExecutorNum() {
        return executorNum;
    }

    public int getProxyChangeInterval() {
        return proxyChangeInterval;
    }

    public int getDomainQueueSize() {
        return domainQueueSize;
    }

    public String getFileStorePath() {
        return fileStorePath;
    }

    public int getDownloadInterval() {
        return downloadInterval;
    }

    public int getRetryTimes() {
        return retryTimes;
    }

    private int getIntProperty(Properties prop, String field, int defaultValue) {
        String strVal = prop.getProperty(field);
        if (strVal == null || strVal.isEmpty()) {
            return defaultValue;
        }
        return Integer.parseInt(strVal);
    }

    private Properties loadConfigs(File configFile) throws IOException {
        Properties prop = new Properties();
        prop.load(new FileReader(configFile));
        return prop;
    }

    public enum Role {
        MASTER,
        SLAVE
    }

}
