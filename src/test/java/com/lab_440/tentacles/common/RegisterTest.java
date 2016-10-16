package com.lab_440.tentacles.common;

import com.lab_440.tentacles.slave.downloader.IDownloader;
import com.lab_440.tentacles.slave.parser.IParser;
import com.lab_440.tentacles.common.item.IItem;
import com.lab_440.tentacles.slave.downloader.BaseDownloader;
import com.lab_440.tentacles.slave.parser.BaseParser;
import io.vertx.core.Handler;
import io.vertx.core.http.HttpClient;
import io.vertx.core.http.HttpClientResponse;
import io.vertx.core.http.HttpMethod;
import org.junit.Assert;
import org.junit.Test;

import java.util.List;

public class RegisterTest {

    @Test
    public void testDefaultRegister() {
        IDownloader dlr = Register.getInstance().getDownloader("www.visualbusiness.com");
        Assert.assertTrue(dlr instanceof BaseDownloader);
        IParser psr = Register.getInstance().getParser("www.visualbusiness.com");
        Assert.assertTrue(psr instanceof BaseParser);
        float itvl = Register.getInstance().getInterval("www.visualbusiness.com");
        Assert.assertTrue(0f == itvl);
    }

    class DummyDownloader implements IDownloader {
        private HttpMethod httpMethod;
        @Override
        public void init() {}

        @Override
        public IDownloader setHTTPMethod(HttpMethod httpMethod) {
            this.httpMethod = httpMethod;
            return this;
        }

        @Override
        public void download(HttpClient httpclient,
                             String url,
                             Handler<HttpClientResponse> handler)
                throws Exception {}
    }

    class DummyParser implements IParser {
        @Override
        public void init() {}
        @Override
        public void parse(String url, String page) throws Exception {}
        @Override
        public ProcessStatus getStatus() { return null; }
        @Override
        public List<IItem> getItems() { return null; }
        @Override
        public List<String> getFollowUrls() { return null; }
    }

    @Test
    public void testRegisterAndGet() {
        Register.getInstance().registerDownloader("www.visualbusiness.com", new DummyDownloader());
        IDownloader dlr = Register.getInstance().getDownloader("www.visualbusiness.com");
        Assert.assertTrue(dlr instanceof DummyDownloader);
        Register.getInstance().registerParser("www.visualbusiness.com", new DummyParser());
        IParser psr = Register.getInstance().getParser("www.visualbusiness.com");
        Assert.assertTrue(psr instanceof DummyParser);
        Register.getInstance().registerInterval("www.visualbusiness.com", 1000);
        float itvl = Register.getInstance().getInterval("www.visualbusiness.com");
        Assert.assertTrue(1000f == itvl);
    }

}
