<li <?= $this->app->checkMenuSelection('PluginConfigsController', 'show', 'OpteiaSkin') ?>>
    <?= $this->url->link(t('Opteia Skin Settings'), 'PluginConfigsController', 'show', array('plugin' => 'OpteiaSkin')) ?>
</li>
