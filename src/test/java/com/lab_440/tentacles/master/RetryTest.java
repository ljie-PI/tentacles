package com.lab_440.tentacles.master;

import com.lab_440.tentacles.common.item.RequestItem;
import org.junit.Assert;
import org.junit.Test;

public class RetryTest {

    @Test
    public void testRetry() {
        Retry retry = new Retry(2);
        String jstr = "{\"url\":\"http://www.visualbusiness.com\"}";
        RequestItem item = new RequestItem().decode(jstr);
        Assert.assertEquals(0, retry.retry(item));
        Assert.assertEquals(0, retry.retry(item));
        Assert.assertEquals(2, retry.retry(item));
    }

}
