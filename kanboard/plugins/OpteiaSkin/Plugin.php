<?php
namespace Kanboard\Plugin\OpteiaSkin;

use Kanboard\Core\Plugin\Base;
use Kanboard\Core\Translator;
use Kanboard\Plugin\OpteiaSkin\Model\TaskInfoCSSModel;


class Plugin extends Base
{
	public function initialize()
	{
		// register helper
		$this->helper->register('configsDataHelper', '\Kanboard\Plugin\OpteiaSkin\Helper\ConfigsDataHelper');
		$this->helper->register('modeSwitchHelper', '\Kanboard\Plugin\OpteiaSkin\Helper\ModeSwitchHelper');
		$this->helper->register('colorSwitchHelper', '\Kanboard\Plugin\OpteiaSkin\Helper\ColorSwitchHelper');

		// add class "TR" to body
		$this->template->setTemplateOverride('layout', 'OpteiaSkin:layout');

		// add logo to page
		$this->template->setTemplateOverride('header/title', 'OpteiaSkin:header/title');
		$this->template->hook->attach('template:auth:login-form:before', 'OpteiaSkin:auth/login_form_before');

		// admin config UI
		$this->route->addRoute('settings/opteia-skin', 'PluginConfigsController', 'show', 'OpteiaSkin');
		$this->template->hook->attach('template:config:sidebar', 'OpteiaSkin:settings/sidebar');

		// set CSP
		$this->setContentSecurityPolicy(array('style-src' => '\'self\' \'unsafe-inline\' fonts.googleapis.com'));

		// load configs
		global $opteiaSkinConfig;
		$opteiaSkinConfig = $this->loadConfigs();

		// init color scheme
		$this->initColorScheme($opteiaSkinConfig['color_scheme']);

		// mode switch
		if (isset($opteiaSkinConfig['mode']) && $opteiaSkinConfig['mode'] == "development") {
			$this->helper->modeSwitchHelper->developmentMode();
		}
		else {
			$this->helper->modeSwitchHelper->productionMode();
		}

		// corner radius
		if (!empty($opteiaSkinConfig['corner_radius'])){
			$this->template->hook->attach('template:layout:head', 'OpteiaSkin:layout/head_corner_radius', array('radius' => $opteiaSkinConfig['corner_radius']));
		}

		// icons replacement
		if (!isset($opteiaSkinConfig['enable_google_material_icons']) || $opteiaSkinConfig['enable_google_material_icons']) {
			$this->hook->on('template:layout:css', array('template' => 'plugins/OpteiaSkin/Asset/material-symbols/index.min.css'));
		}

		// google fonts
		if (isset($opteiaSkinConfig['google_fonts'])){
			$this->template->hook->attach('template:layout:head', 'OpteiaSkin:layout/head_google_fonts', array('configs' => $opteiaSkinConfig['google_fonts']));
		}

		// syntax highlight
		$this->hook->on('template:layout:css', array('template' => 'plugins/OpteiaSkin/Asset/highlight/style.min.css'));
		$this->hook->on('template:layout:js', array('template' => 'plugins/OpteiaSkin/Asset/highlight/highlight.min.js'));

		// main js
		$this->hook->on('template:layout:js', array('template' => 'plugins/OpteiaSkin/Asset/main.min.js'));

		// Opteia overrides — registered LAST so they win over main.min.css + all other assets
		$this->hook->on('template:layout:css', array('template' => 'plugins/OpteiaSkin/Asset/opteia-overrides.css'));

		// PWA: installable manifest + service worker + theme-color (head tags)
		$this->template->hook->attach('template:layout:head', 'OpteiaSkin:layout/head_pwa');
	}

	public function onStartup(){
		// load translations
		Translator::load($this->languageModel->getCurrentLanguage(), __DIR__.'/Locale');

		// enable custom task display (the css selectors depend on localized text)
		$this->enableCustomTaskDisplay($GLOBALS['opteiaSkinConfig']);
	}

	public function getPluginName() {
		return 'Opteia Skin';
	}

	public function getPluginAuthor() {
		return 'Opteia (based on ThemeRevision by Greyaz)';
	}

	public function getPluginVersion() {
		return '1.0.0';
	}

	public function getPluginDescription() {
		return "Modern, responsive Opteia skin for Kanboard. Auto light/dark, mobile-first. Based on ThemeRevision (MIT) by Greyaz.";
	}

	public function getPluginHomepage() {
		return 'https://github.com/greyaz/ThemeRevision';
	}

	private function loadConfigs() {
		$configs;
		$defConfigs = $this->helper->configsDataHelper->getDefaultConfigs();
        $dbConfigs = $this->helper->configsDataHelper->loadConfigs();
        $oldConfigs = $this->helper->configsDataHelper->calcOldConfigs($dbConfigs);
        //old user, need update
        if (!empty($oldConfigs)){
			// check color diffs
            $colorDiffs = $this->helper->configsDataHelper->calcColorDiffs($oldConfigs);
            if (!empty($colorDiffs)){
                $this->helper->configsDataHelper->saveColorDiffs($colorDiffs);
            }
			// merged configs
            $mergedConfigs = $this->helper->configsDataHelper->calcMergedConfigs($oldConfigs, $defConfigs);
			// load and save configs
            $configs = $mergedConfigs;
            $this->helper->configsDataHelper->saveConfigs($configs);
        }
        //old user, need not update
        elseif (!empty($dbConfigs)){
			// load configs
            $configs = $dbConfigs;
        }
        //new user
        else {
			// load and save configs
            $configs = $defConfigs;
            $this->helper->configsDataHelper->saveConfigs($configs);
        }
		return $configs;
	}

	private function initColorScheme($colorScheme) {
		if (isset($colorScheme) && $colorScheme == "light") {
			$this->helper->colorSwitchHelper->setColor2Light();
		}
		elseif (isset($colorScheme) && $colorScheme == "dark"){
			$this->helper->colorSwitchHelper->setColor2Dark();
		}
		else {
			// user config UI
			$this->route->addRoute('user/:user_id/theme', 'UserSettingsController', 'show', 'OpteiaSkin');
			$this->template->hook->attach('template:user:sidebar:actions', 'OpteiaSkin:user/sidebar');
			$this->template->hook->attach('template:header:dropdown', 'OpteiaSkin:user/header_dropdown');
			$this->helper->colorSwitchHelper->setColorByUser();
		}
	}

	private function enableCustomTaskDisplay($config){
		// adjust column and task info
		$columnList = array();
		$taskList = array();
		foreach($config['column_header_info'] as $key => $value){
			if ($value == false){
				$columnList[] = $key;
			}
		}
		foreach($config['board_task_info'] as $key => $value){
			if ($value == false){
				$taskList[] = $key;
			}
		}
		$this->template->hook->attach('template:layout:head', 'OpteiaSkin:layout/head_task_info_display', array(
			'styles' 	=> TaskInfoCSSModel::getFullCSS($columnList, $taskList), 
			'opacity' 	=> $config['task_footer_opacity']
		));
	}
}
