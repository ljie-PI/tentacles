package com.lab_440.tentacles.common;

import org.junit.Assert;
import org.junit.Test;

public class IDGeneratorTest {

    @Test
    public void testIDGenerator() {
        Assert.assertEquals("47bce5c74f589f4867dbd57e9ca9f808",
                IDGenerator.generateID("aaa"));
        IDGenerator.setSeed(10000);
        Assert.assertEquals("e8de85ef4c7b9f34821d1e44f1a16d5a",
                IDGenerator.generateRandom());
    }

}
