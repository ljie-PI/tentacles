package com.lab_440.tentacles.common;

import com.lab_440.tentacles.common.item.ResultItem;
import com.lab_440.tentacles.common.item.RequestItem;
import org.junit.Assert;
import org.junit.Test;

public class ItemTest {

    @Test
    public void testResultItem() {
        String jstr = "{\"title\":\"test page\"}";
        ResultItem item = (ResultItem) new ResultItem().decode(jstr);
        item.setUrl("http://www.visualbusiness.com");
        Assert.assertEquals("http://www.visualbusiness.com\u0001test page",
                item.identity());
        Assert.assertEquals("{\"ITEM_TYPE\":\"result\",\"url\":\"http://www.visualbusiness.com\",\"title\":\"test page\"}",
                item.encode());
    }

    @Test
    public void testRequestItem() {
        String jstr = "{\"url\":\"http://www.visualbusiness.com\"}";
        RequestItem item = (RequestItem) new RequestItem().decode(jstr);
        Assert.assertEquals("http://www.visualbusiness.com",
                item.identity());
        Assert.assertEquals("{\"url\":\"http://www.visualbusiness.com\",\"is_retry\":false}",
                item.encode());
    }

}
